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
# `nat64` (#572): NAT64's pools, written under `nat` > `nat64` since
# 2026-10-05, read and written apart from `pools` - which since then holds the
# CGNAT pools only.
CONFIG_AREAS = ("interfaces", "pools", "nat64", "logging")
ROUTING_AREAS = ("static", "bgp", "ospf", "ospf6", "isis", "interface_routing",
                 "bfd")
#: The areas of #558 phase 4 - system, accounts, policy and the rest; kept
#: in step with SYSTEM_AREAS below, which the readers are keyed by.
OTHER_AREAS = ("system", "users", "snmp_users", "policy", "route_maps",
               "frr_lists", "rpki", "routing_attachments", "vrfs", "vrrp",
               "bundles", "cgnat_globals")
AREAS = CONFIG_AREAS + ROUTING_AREAS + OTHER_AREAS

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
        "pref64": list(cfg.pref64.get(name, [])),
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
        # #573: the statement's word, `bpa` for on demand.
        "mode": pool.mode_word,
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
        "subscriber_length": int(pool.subscriber_length),
        "nat64_prefix": pool.nat64_prefix,
    }


def pools_typed(cfg):
    """Every CGNAT pool of `cfg`, by name - those under `nat` > `cgnat`."""
    return [pool_typed(cfg.pools[n]) for n in sorted(cfg.pools)
            if getattr(cfg.pools[n], "level", "cgnat") == "cgnat"]


def nat64_pool_typed(pool):
    """
    One NAT64 pool as Nat64PoolConfig's fields (#572): a pool's, less the
    bypass a NAT64 pool never takes - refused for good by #485, so a field
    for it would be a field nothing can ever set.
    """
    d = pool_typed(pool)
    for k in ("bypass_source", "bypass_destination"):
        d.pop(k)
    return d


def nat64_pools_typed(cfg):
    """Every NAT64 pool of `cfg`, by name - those under `nat` > `nat64`."""
    return [nat64_pool_typed(cfg.pools[n]) for n in sorted(cfg.pools)
            if getattr(cfg.pools[n], "level", "cgnat") == "nat64"]


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
    if "nat64" in want:
        out["nat64"] = nat64_pools_typed(cfg)
    if "logging" in want:
        out["logging"] = logging_typed(cfg)
    if want & set(ROUTING_AREAS):
        out.update(routing_typed(cfg, want & set(ROUTING_AREAS)))
    if want & set(OTHER_AREAS):
        out.update(system_areas_typed(cfg, want & set(OTHER_AREAS)))
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
    # The route's BFD words (#559): FRR writes them last, so everything from
    # `bfd` on is the session's. As fields, so a client writing the route
    # back puts them where `router static` takes them - after the distance
    # and tag - rather than as `extra`, which goes before them.
    out.update(bfd=False, bfd_multihop=False, bfd_source="", bfd_profile="")
    if "bfd" in out["extra"]:
        at = out["extra"].index("bfd")
        tail, out["extra"] = out["extra"][at + 1:], out["extra"][:at]
        out["bfd"] = True
        j = 0
        while j < len(tail):
            if tail[j] == "multi-hop":
                out["bfd_multihop"] = True
                j += 1
            elif tail[j] in ("source", "profile") and j + 1 < len(tail):
                out["bfd_" + tail[j]] = tail[j + 1]
                j += 2
            else:
                out["extra"] += tail[j:]
                break
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
                      "shutdown": False, "bfd": False, "bfd_profile": "",
                      "other": []}
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
        # FRR reads a neighbour with a profile back as TWO lines, `bfd` and
        # `bfd profile P` (#570); the profile was dropped here until #559's
        # phase 3, and a client writing the neighbour back lost it.
        nb["bfd"] = True
        if len(v) == 2 and v[0] == "profile":
            nb["bfd_profile"] = v[1]
        elif v:
            nb["other"].append("neighbor " + nb["address"] + " " + " ".join(w))
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


# --------------------------------------------------------- BFD (#559 phase 3)
#
# The `bfd` block, read off the same statements the screens print: its
# `peer` sessions and its `profile` timer sets. A peer IS its header - FRR
# keys a session on the address, multihop, local address, interface and VRF -
# so the header is read into those fields and matched by them, never by its
# text: FRR writes the words in its own order, a client may give them in any.
#
# A block may appear more than once in a candidate - every SetConfig enters
# `bfd` again and leaves it, and this VTY stages the second `bfd` as a second
# block (measured: FRR merges them at the commit) - so the reader merges
# sessions and profiles by their key across every `bfd` block.

#: The timers a peer and a profile both take: (field, FRR's word, number?).
BFD_TIMER_WORDS = (("detect_multiplier", "detect-multiplier", True),
                   ("receive_interval_ms", "receive-interval", True),
                   ("transmit_interval_ms", "transmit-interval", True),
                   ("minimum_ttl", "minimum-ttl", True),
                   ("echo_mode", "echo-mode", False),
                   ("passive_mode", "passive-mode", False),
                   ("shutdown", "shutdown", False))
BFD_TIMER_FIELDS = tuple(f for f, _w, _n in BFD_TIMER_WORDS) + (
    "echo_receive_interval_ms", "echo_receive_disabled",
    "echo_transmit_interval_ms")
BFD_PEER_FIELDS = ("profile", "timers")
BFD_PROFILE_FIELDS = ("timers",)
BFD_PATH = ["bfd"]
BFD_KEY = ("address", "multihop", "local_address", "interface", "vrf")


def bfd_timers_blank():
    """A body with nothing stated: 0 / False is FRR's default."""
    return {f: (False if f in ("echo_mode", "passive_mode", "shutdown",
                               "echo_receive_disabled") else 0)
            for f in BFD_TIMER_FIELDS}


def bfd_peer_key(header):
    """`peer A [multihop] [local-address L] [interface I] [vrf V]` as the
    session's key fields, in whatever order the words came."""
    w = header.split()
    if len(w) < 2 or w[0] != "peer":
        raise ValueError(f"bfd: not a peer: {header!r}")
    out = {"address": w[1], "multihop": False, "local_address": "",
           "interface": "", "vrf": ""}
    i = 2
    while i < len(w):
        if w[i] == "multihop":
            out["multihop"] = True
            i += 1
        elif w[i] in ("local-address", "interface", "vrf") and i + 1 < len(w):
            out[w[i].replace("-", "_")] = w[i + 1]
            i += 2
        else:
            raise ValueError(f"bfd: unknown word {w[i]!r} in {header!r}")
    return out


def bfd_peer_header(p):
    """The `peer ...` line of a session's key fields, in FRR's own order."""
    if not p.get("address"):
        raise ValueError("bfd peer: give its address")
    for k in ("local_address", "interface", "vrf"):
        if len((p.get(k) or "x").split()) != 1:
            raise ValueError(f"bfd peer: {k} is one word")
    line = f"peer {p['address']}"
    if p.get("multihop"):
        line += " multihop"
    for f, word in (("local_address", "local-address"),
                    ("interface", "interface"), ("vrf", "vrf")):
        if p.get(f):
            line += f" {word} {p[f]}"
    return line


def same_bfd_peer(a, b):
    """Are `a` and `b` one session: the same key, whatever else differs."""
    return all(bool(a.get(k)) == bool(b.get(k)) if k == "multihop"
               else (a.get(k) or "") == (b.get(k) or "") for k in BFD_KEY)


def bfd_body_statement(t, other, stmt):
    """One statement of a peer's or a profile's body, into timers `t`; what
    no field takes goes to `other` verbatim."""
    w = stmt.split()
    for f, word, number in BFD_TIMER_WORDS:
        if w[0] != word:
            continue
        if number and len(w) == 2 and is_number(w[1]):
            t[f] = _int(w[1])
            return
        if not number and len(w) == 1:
            t[f] = True
            return
    if len(w) == 3 and w[0] == "echo" and w[1] == "receive-interval":
        if w[2] == "disabled":
            t["echo_receive_disabled"] = True
            return
        if is_number(w[2]):
            t["echo_receive_interval_ms"] = _int(w[2])
            return
    if len(w) == 3 and w[:2] == ["echo", "transmit-interval"] and \
            is_number(w[2]):
        t["echo_transmit_interval_ms"] = _int(w[2])
        return
    other.append(stmt)


def bfd_peer_of(peers, header):
    """The session `header` names in list `peers`, added when it is new: one
    entry per key, however many blocks state it."""
    key = bfd_peer_key(header)
    p = next((x for x in peers if same_bfd_peer(x, key)), None)
    if p is None:
        p = dict(key, profile="", timers=bfd_timers_blank(), other=[],
                 header=bfd_peer_header(key))
        peers.append(p)
    return p


def bfd_profile_of(profiles, name):
    """Profile `name` in list `profiles`, added when it is new."""
    p = next((x for x in profiles if x["name"] == name), None)
    if p is None:
        p = {"name": name, "timers": bfd_timers_blank(), "other": []}
        profiles.append(p)
    return p


def bfd_typed(cfg):
    """{"peers": [...], "profiles": [...]} of every `bfd` block in `cfg`."""
    peers, profiles = [], []
    peer_of = (lambda header: bfd_peer_of(peers, header))
    profile_of = (lambda name: bfd_profile_of(profiles, name))
    for path, stmt in routing_statements(cfg):
        if not path or path[0] != "bfd":
            continue
        w = stmt.split()
        if len(path) == 1:
            if w[0] == "peer":
                peer_of(stmt)
            elif w[0] == "profile" and len(w) == 2:
                profile_of(w[1])
            continue
        head = path[1].split()
        if head[0] == "peer":
            p = peer_of(path[1])
            if w[0] == "profile" and len(w) == 2:
                p["profile"] = w[1]
            else:
                bfd_body_statement(p["timers"], p["other"], stmt)
        elif head[0] == "profile" and len(head) == 2:
            p = profile_of(head[1])
            bfd_body_statement(p["timers"], p["other"], stmt)
    return {"peers": peers, "profiles": profiles}


def _echo_rx(t):
    if t.get("echo_receive_disabled"):
        return "disabled"
    return str(int(t.get("echo_receive_interval_ms") or 0) or "")


def bfd_timer_lines(now, want, fields=()):
    """
    The body lines that take timers `now` to `want`, for the timer `fields`
    named (all when none are). A value is removed as `no <the line as it
    is>`, which this VTY reads as taking that very line out of the candidate
    (#570) - measured: a bare `no detect-multiplier` is staged as a statement
    of its own and leaves the old line in the candidate, so a read of the
    candidate after it would still show the old value.
    """
    now = now or bfd_timers_blank()
    want = want or {}
    on = (lambda f: not fields or f in fields)
    lines = []
    for f, word, number in BFD_TIMER_WORDS:
        if not on(f):
            continue
        a = int(now.get(f) or 0) if number else bool(now.get(f))
        b = int(want.get(f) or 0) if number else bool(want.get(f))
        if a == b:
            continue
        if number:
            lines += ([f"no {word} {a}"] if a else []) + \
                ([f"{word} {b}"] if b else [])
        else:
            lines.append(word if b else f"no {word}")
    if on("echo_receive_interval_ms") or on("echo_receive_disabled"):
        a, b = _echo_rx(now), _echo_rx(want)
        if a != b:
            lines += ([f"no echo receive-interval {a}"] if a else []) + \
                ([f"echo receive-interval {b}"] if b else [])
    if on("echo_transmit_interval_ms"):
        a = int(now.get("echo_transmit_interval_ms") or 0)
        b = int(want.get("echo_transmit_interval_ms") or 0)
        if a != b:
            lines += ([f"no echo transmit-interval {a}"] if a else []) + \
                ([f"echo transmit-interval {b}"] if b else [])
    return lines


def bfd_timer_mask(fields):
    """(the plain fields, the timer fields) of an edit's `fields`: `timers`
    names every timer, `timers.<field>` one; None is no timer at all."""
    plain = [f for f in fields if not f.startswith("timers.")]
    named = [f.split(".", 1)[1] for f in fields if f.startswith("timers.")]
    bad = [f for f in named if f not in BFD_TIMER_FIELDS]
    if bad:
        raise ValueError(f"unknown field 'timers.{bad[0]}': the timer fields "
                         f"are {', '.join(BFD_TIMER_FIELDS)}")
    if not fields or "timers" in plain:
        return plain, ()
    return plain, (named or None)


def bfd_peer_lines(cur, desired, fields=()):
    """The lines, at `bfd`, that set one session: its header, the body lines
    that change, `exit`. `cur` is the session as bfd_typed reads it from the
    candidate, None when it is new."""
    plain, timers = bfd_timer_mask(fields)
    _mask(plain, BFD_PEER_FIELDS)
    cur = cur or {}
    body = []
    # `fields` empty is every field; `timers.<f>` alone names no plain one.
    # Measured in lab/api-grpc.sh: `["timers.transmit_interval_ms"]` read
    # as "no plain field named, so all of them" wrote `no profile api9`.
    if not fields or "profile" in plain:
        a, b = cur.get("profile") or "", desired.get("profile") or ""
        if len(b.split()) > 1:
            raise ValueError("bfd peer: a profile name is one word")
        if a != b:
            body += ([f"no profile {a}"] if a else []) + \
                ([f"profile {b}"] if b else [])
    if timers is not None:
        body += bfd_timer_lines(cur.get("timers"), desired.get("timers"),
                                timers)
    return [bfd_peer_header(desired)] + body + ["exit"]


def bfd_profile_lines(cur, desired, fields=()):
    """The lines, at `bfd`, that set one profile."""
    plain, timers = bfd_timer_mask(fields)
    _mask(plain, BFD_PROFILE_FIELDS)
    name = desired.get("name") or ""
    if len(name.split()) != 1:
        raise ValueError("bfd profile: give its name, one word")
    body = [] if timers is None else bfd_timer_lines(
        (cur or {}).get("timers"), desired.get("timers"), timers)
    return [f"profile {name}"] + body + ["exit"]


def bfd_delete_lines(cfg, name="", peer=None):
    """(path, lines) that take a profile (`name`), a session (`peer`, its
    key fields) or - neither given - the whole `bfd` block out of `cfg`. Each
    as the `no` of its header, which takes its body with it (#570)."""
    cur = bfd_typed(cfg)
    if peer and peer.get("address"):
        p = next((x for x in cur["peers"] if same_bfd_peer(x, peer)), None)
        if p is None:
            raise ValueError(f"bfd: no session {bfd_peer_header(peer)!r}")
        return list(BFD_PATH), [f"no {p['header']}"]
    if name:
        if not any(x["name"] == name for x in cur["profiles"]):
            raise ValueError(f"bfd: no profile {name}")
        return list(BFD_PATH), [f"no profile {name}"]
    if not any(s == "bfd" and p == [] for p, s in routing_statements(cfg)):
        raise ValueError("bfd: no bfd block to remove")
    return [], ["no bfd"]


# ------------------------------------------------- routing edits (phase 3)

STATIC_FIELDS = ("next_hop", "distance", "tag", "description", "extra",
                 "bfd", "bfd_multihop", "bfd_source", "bfd_profile")
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
    if r.get("bfd"):
        line += " bfd"
        if r.get("bfd_multihop"):
            line += " multi-hop"
        for k in ("source", "profile"):
            v = r.get("bfd_" + k) or ""
            if len(v.split()) > 1:
                raise ValueError(f"static route: bfd {k} is one word")
            if v:
                line += f" {k} {v}"
    elif r.get("bfd_multihop") or r.get("bfd_source") or r.get("bfd_profile"):
        raise ValueError("static route: a BFD option needs bfd")
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
                   "bfd", "bfd_profile", "families")
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
    # The profile only while BFD stays on: `no neighbor X bfd` takes the
    # profile line with it (#570), and a second `no` for the profile would
    # name a line that is no longer there.
    if on("bfd_profile") and (want.get("bfd") or (not on("bfd") and
                                                   cur.get("bfd"))):
        a, b = cur.get("bfd_profile") or "", want.get("bfd_profile") or ""
        if len(b.split()) > 1:
            raise ValueError("bgp neighbor: a BFD profile name is one word")
        if a != b:
            lines += ([f"no neighbor {n} bfd profile {a}"] if a else []) + \
                ([f"neighbor {n} bfd profile {b}"] if b else [])
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
    stmts = [(p, s) for p, s in routing_statements(cfg)]
    # `no neighbor X bfd` takes FRR's second line, `bfd profile P`, with it
    # (#570): a `no` for that line too names one that is gone, and measured
    # in lab/api-grpc.sh (#559 phase 3) the commit then failed "routing
    # reload failed". So the profile line is left to the `bfd` one.
    plain_bfd = any(p and p[0] == header and s.split() == ["neighbor", name,
                                                          "bfd"]
                    for p, s in stmts)
    for path, stmt in stmts:
        w = stmt.split()
        if not path or path[0] != header or w[:2] != ["neighbor", name]:
            continue
        if plain_bfd and w[2:4] == ["bfd", "profile"]:
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
               asn="", vrf="", bfd_peer=None):
    """(path, lines) that remove one object from Config `cfg` - the
    candidate: a BGP neighbour is removed by its own statements, read off
    `cfg`, and so is a BFD session (by the header the candidate holds it
    under); everything else as delete_lines says."""
    if area == "bfd":
        return bfd_delete_lines(cfg, name, bfd_peer)
    if area == "bgp":
        if not name:
            raise ValueError("bgp: name the neighbour to remove")
        return bgp_path(asn, vrf), bgp_neighbor_delete_lines(cfg, asn, vrf,
                                                             name)
    return delete_lines(area, name, transport, prefix, next_hop, asn, vrf)


#: The knobs of a routing block that phase 3 kept verbatim in `other`, read
#: as {key: value} settings since phase 4: the key is the statement's own
#: keywords, the value the rest ("" for a flag). Longest key first, so
#: `default-information originate` wins over a shorter prefix. A key that
#: names a password gives `<set>`, never the password.
SETTING_KEYS = {
    "bgp": ("bgp log-neighbor-changes", "bgp graceful-shutdown",
            "bgp default ipv4-unicast", "bgp bestpath as-path multipath-relax",
            "bgp bestpath", "bgp cluster-id", "bgp confederation",
            "table-policy", "table-map", "timers bgp", "maximum-paths"),
    "family": ("table-policy", "table-map", "vrf-import", "import",
               "maximum-paths", "distance"),
    "neighbor": ("maximum-prefix", "next-hop-self", "capability",
                 "remove-private-AS", "advertisement-interval", "ttl-security",
                 "local-as", "allowas-in", "solo", "passive", "port",
                 "enforce-first-as"),
    "neighbor_family": ("remove-private-AS", "advertise-map", "advertise",
                        "addpath-tx-all-paths", "allowas-in", "attribute-unchanged",
                        "as-override", "weight", "unsuppress-map",
                        "route-reflector-client", "route-server-client"),
    "ospf": ("export", "auto-cost reference-bandwidth", "maximum-paths",
             "log-adjacency-changes", "timers throttle spf",
             "timers throttle lsa", "distance", "neighbor",
             "default-information originate", "compatible rfc1583",
             "capability opaque", "max-metric"),
    "isis": ("topology ipv6-unicast", "set-overload-bit", "export",
             "default-information originate", "domain-password",
             "area-password", "log-adjacency-changes", "lsp-mtu",
             "lsp-gen-interval", "spf-interval", "max-lsp-lifetime",
             "lsp-refresh-interval", "purge-originator"),
}
_SECRET_KEYS = ("domain-password", "area-password")


def split_settings(other, kind, strip_words=0):
    """
    (settings, rest) of a block's verbatim `other` statements: each one a
    SETTING_KEYS[kind] key starts becomes {"key", "value"}; the rest stay,
    verbatim. `strip_words` drops leading words first - a neighbour's
    statements start `neighbor <address>`.
    """
    keys = sorted(SETTING_KEYS[kind], key=lambda k: -len(k.split()))
    settings, rest = [], []
    for stmt in other:
        w = stmt.split()[strip_words:]
        for k in keys:
            kw = k.split()
            if w[:len(kw)] == kw:
                value = " ".join(w[len(kw):])
                if k in _SECRET_KEYS and value:
                    value = "<set>"
                settings.append({"key": k, "value": value})
                break
        else:
            rest.append(stmt)
    return settings, rest


def _with_settings(block, kind, strip_words=0):
    """Block dict `block` with its `other` split into settings and rest."""
    block["settings"], block["other"] = split_settings(
        block.get("other", []), kind, strip_words)
    return block


def routing_settings(out):
    """routing_typed's answer with every block's knobs read as settings."""
    for b in out.get("bgp", []):
        _with_settings(b, "bgp")
        for n in b.get("neighbors", []):
            _with_settings(n, "neighbor", 2)
        for f in b.get("families", []):
            _with_settings(f, "family")
            for n in f.get("neighbors", []):
                _with_settings(n, "neighbor_family", 2)
    for key in ("ospf", "ospf6"):
        for b in out.get(key, []):
            _with_settings(b, "ospf")
    for b in out.get("isis", []):
        _with_settings(b, "isis")
    return out


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
    if "bfd" in areas:
        b = bfd_typed(cfg)
        out["bfd_peers"], out["bfd_profiles"] = b["peers"], b["profiles"]
    return routing_settings(out)


# ----------------------------------- system, users, policies (phase 4)
#
# The rest of the configuration (#558 phase 4): the box's own settings, the
# accounts, SNMP's users, the Junos-style policy, and what is left of the
# routing half - FRR's lists, the route-maps an operator wrote, RPKI, the
# protocol attachments - plus the VRF devices, the virtual routers and the
# LACP bundles. Each is read off the same Config the VTY prints, never off a
# screen. SECRETS ARE NEVER READ: a password is `password_set`, an SNMP
# community is counted, an SNMP user's passphrases are only their protocol,
# a key is counted - a client is told what is there, not what it is.

SYSTEM_AREAS = ("system", "users", "snmp_users", "policy", "route_maps",
                "frr_lists", "rpki", "routing_attachments", "vrfs", "vrrp",
                "bundles", "cgnat_globals")


def _kv(d):
    """{k: v} as [{"key", "value"}] in key order: maps go out as pairs."""
    return [{"key": k, "value": str(v)} for k, v in sorted(d.items())]


def system_typed(cfg):
    """The box's own settings: name, resolvers, SSH, the API, the SNMP
    agent (communities counted, never shown), the dataplane card and the
    cores and memory by role."""
    snmp = dict(cfg.snmp)
    return {
        "hostname": cfg.hostname,
        "dns_servers": list(cfg.dns_servers),
        "ssh": {"port": _int(cfg.ssh.get("port")),
                "listen_address": cfg.ssh.get("listen-address", ""),
                "permit_root_login": cfg.ssh.get("permit-root-login", ""),
                "password_authentication":
                    cfg.ssh.get("password-authentication") == "yes"},
        "api": {"enabled": cfg.api.get("enabled") == "yes",
                "listen_address": cfg.api.get("listen-address", ""),
                "port": _int(cfg.api.get("port"))},
        "snmp": {"enabled": snmp.get("enabled") == "yes",
                 "listen_address": snmp.get("listen-address", ""),
                 "port": _int(snmp.get("port")),
                 "enterprise_number": _int(snmp.get("enterprise-number")),
                 "communities": len(cfg.snmp_communities),
                 "trap_hosts": [{"address": a, "inform": kind == "inform"}
                                for a, _community, kind
                                in cfg.snmp_trap_hosts]},
        "dataplane_interface": cfg.dataplane_if,
        "cgnat_memory_gb": cfg.cgnat_memory_gb,
        "cpu": _kv(cfg.cpu),
        "memory": _kv(cfg.memory),
    }


def users_typed(cfg):
    """The accounts: name, role ("" is read only), how many SSH keys, and
    whether a password is set - never the hash, never a key."""
    return [{"name": n, "role": u.role, "ssh_keys": len(u.keys),
             "password_set": bool(u.password),
             "operator": bool(getattr(u, "operator", False))}
            for n, u in sorted(cfg.users.items())]


def snmp_users_typed(cfg):
    """SNMPv3 users: the protocols, never the passphrases."""
    return [{"name": n,
             "auth_protocol": (u.auth.split() or [""])[0],
             "priv_protocol": (u.priv.split() or [""])[0]}
            for n, u in sorted(cfg.snmp_users.items())]


#: A term's `from` conditions, as the attribute policy.Term keeps them and
#: the word the VTY takes them by.
_FROM_KINDS = (("prefix_lists", "prefix-list"), ("communities", "community"),
               ("as_paths", "as-path"), ("protocols", "protocol"),
               ("tags", "tag"), ("metrics", "metric"),
               ("neighbors", "neighbor"), ("families", "family"),
               ("origins", "origin"), ("local_prefs", "local-preference"),
               ("next_hops", "next-hop"),
               ("large_communities", "large-community"),
               ("ext_communities", "extended-community"),
               ("validation", "validation-state"), ("policies", "policy"),
               ("source_vrfs", "source-vrf"))


def _filter_text(f):
    """A route-filter (network, kind, upto) as the VTY writes it."""
    network, kind, upto = f
    return " ".join(x for x in (network, kind,
                                str(upto) if upto is not None else "") if x)


def policy_typed(cfg):
    """
    The Junos-style policy (#458): the named lists and every
    policy-statement with its terms, each term's conditions by kind, its
    changes and its decision - read by policy.parse, the parser the commit
    compiles from. A line that parser cannot read is in `unread`, verbatim,
    with why.
    """
    import policy as policy_mod
    pol, bad = policy_mod.parse(cfg.policy)
    statements = []
    for name, terms in pol.statements.items():
        out_terms = []
        for tname, t in terms.items():
            frm = [{"kind": word, "values": [str(v) for v in getattr(t, attr)]}
                   for attr, word in _FROM_KINDS if getattr(t, attr)]
            if t.filters:
                frm.append({"kind": "route-filter",
                            "values": [_filter_text(f) for f in t.filters]})
            out_terms.append({
                "name": tname, "from": frm,
                "sets": [{"what": what, "args": [str(a) for a in args]}
                         for what, args in t.sets],
                "then": t.then or ""})
        statements.append({"name": name,
                           "default_action": pol.defaults.get(name, ""),
                           "terms": out_terms})
    return {
        "prefix_lists": [{"name": n, "members": list(v)}
                         for n, v in pol.prefix_lists.items()],
        "communities": [{"name": n, "members": list(v)}
                        for n, v in pol.communities.items()],
        "large_communities": [{"name": n, "members": list(v)}
                              for n, v in pol.large_communities.items()],
        "extended_communities": [
            {"name": n, "members": [f"{kind}:{v}" for kind, v in members]}
            for n, members in pol.ext_communities.items()],
        "as_paths": [{"name": n, "regex": v} for n, v in pol.as_paths.items()],
        "statements": statements,
        "unread": [{"line": line, "why": why} for line, why in bad],
    }


#: The prefix of the route-maps the policy compiler writes (policy.py): the
#: box's, compiled from policy-options, never an operator's.
COMPILED_MAP = "PO-"


def _top_statements(cfg):
    """The routing half's statements at the top level, in order."""
    return [line for path, line in routing_statements(cfg) if path == []]


def route_maps_typed(cfg):
    """
    The route-maps an operator wrote in the routing half: each entry's
    action and sequence, its match and set lines, call, on-match, and the
    rest verbatim. The compiled `PO-` maps are policy-options' and are read
    there, not here.
    """
    entries = {}
    order = []
    for path, line in routing_statements(cfg):
        if path == []:
            w = line.split()
            if w[:1] == ["route-map"] and len(w) >= 4 \
                    and not w[1].startswith(COMPILED_MAP):
                key = line
                entries[key] = {"name": w[1], "action": w[2],
                                "sequence": _int(w[3]), "matches": [],
                                "sets": [], "call": "", "on_match": "",
                                "description": "", "other": []}
                order.append(key)
            continue
        head = path[0] if path else ""
        if head not in entries:
            continue
        e, w = entries[head], line.split()
        if w[0] == "match":
            e["matches"].append(" ".join(w[1:]))
        elif w[0] == "set":
            e["sets"].append(" ".join(w[1:]))
        elif w[0] == "call" and len(w) > 1:
            e["call"] = w[1]
        elif w[0] == "on-match":
            e["on_match"] = " ".join(w[1:])
        elif w[0] == "description":
            e["description"] = " ".join(w[1:])
        else:
            e["other"].append(line)
    maps = {}
    for key in order:
        e = entries[key]
        maps.setdefault(e.pop("name"), []).append(e)
    return [{"name": n, "entries": es} for n, es in maps.items()]


def _list_entry(w, i):
    """`[seq N] permit|deny <value...>` from word i: (seq, action, rest)."""
    seq = 0
    if w[i:i + 1] == ["seq"] and len(w) > i + 1:
        seq, i = _int(w[i + 1]), i + 2
    action = w[i] if len(w) > i else ""
    return seq, action, w[i + 1:]


def frr_lists_typed(cfg):
    """
    The lists written for FRR in the routing half: prefix-lists (each
    entry's prefix and ge/le), community and large-community lists, and
    AS-path access-lists. policy-options' own named lists are read in
    `policy`.
    """
    prefix, bgp = {}, {}
    for line in _top_statements(cfg):
        w = line.split()
        if w[:2] in (["ip", "prefix-list"], ["ipv6", "prefix-list"]) \
                and len(w) > 3:
            fam = "ipv4" if w[0] == "ip" else "ipv6"
            if w[3] == "description":
                continue
            seq, action, rest = _list_entry(w, 3)
            e = {"sequence": seq, "action": action,
                 "prefix": rest[0] if rest else "", "ge": 0, "le": 0}
            for k in ("ge", "le"):
                if k in rest and rest.index(k) + 1 < len(rest):
                    e[k] = _int(rest[rest.index(k) + 1])
            prefix.setdefault((fam, w[2]), []).append(e)
        elif w[:2] == ["bgp", "as-path"] and len(w) > 4 \
                and w[2] == "access-list":
            seq, action, rest = _list_entry(w, 4)
            bgp.setdefault(("as-path", "", w[3]), []).append(
                {"sequence": seq, "action": action, "value": " ".join(rest)})
        elif w[:1] == ["bgp"] and len(w) > 3 and w[1] in (
                "community-list", "large-community-list",
                "extcommunity-list"):
            i, style = 2, ""
            if w[2] in ("standard", "expanded"):
                style, i = w[2], 3
            name = w[i] if len(w) > i else ""
            seq, action, rest = _list_entry(w, i + 1)
            kind = w[1][:-len("-list")]
            bgp.setdefault((kind, style, name), []).append(
                {"sequence": seq, "action": action, "value": " ".join(rest)})
    return {
        "prefix_lists": [{"family": fam, "name": n, "entries": es}
                         for (fam, n), es in prefix.items()],
        "bgp_lists": [{"kind": kind, "style": style, "name": n, "entries": es}
                      for (kind, style, n), es in bgp.items()],
    }


def rpki_typed(cfg):
    """The RPKI validator caches and timers (the `rpki` block)."""
    out = {"caches": [], "polling_period": 0, "expire_interval": 0,
           "retry_interval": 0, "other": []}
    for path, line in routing_statements(cfg):
        if path != ["rpki"]:
            continue
        w = line.split()
        if w[:2] == ["rpki", "cache"] and len(w) > 4:
            transport, i = "tcp", 2
            if w[2] in ("tcp", "ssh"):
                transport, i = w[2], 3
            c = {"transport": transport, "host": w[i],
                 "port": _int(w[i + 1]), "user": "", "source": "",
                 "preference": 0}
            rest = w[i + 2:]
            if transport == "ssh" and rest and rest[0] not in (
                    "source", "preference"):
                c["user"] = rest[0]
            for k in ("source", "preference"):
                if k in rest and rest.index(k) + 1 < len(rest):
                    v = rest[rest.index(k) + 1]
                    c[k] = _int(v) if k == "preference" else v
            out["caches"].append(c)
        elif w[:1] == ["rpki"] and len(w) == 3 and w[1] in (
                "polling_period", "expire_interval", "retry_interval"):
            out[w[1]] = _int(w[2])
        else:
            out["other"].append(line)
    return out


def routing_attachments_typed(cfg):
    """
    Where a route-map or a policy is attached outside a protocol's block:
    `ip|ipv6 protocol <p> route-map <m>` (zebra's install filter) and
    `fib-policy <p> <policy>` (#536). What else stands at the top of the
    routing half - a list, a route-map's header, a block - is read by its
    own area; the rest is in `other`, verbatim.
    """
    att, other = [], []
    for line in _top_statements(cfg):
        w = line.split()
        if w[:2] in (["ip", "protocol"], ["ipv6", "protocol"]) and len(w) == 5 \
                and w[3] == "route-map":
            att.append({"kind": "protocol-route-map",
                        "family": "ipv4" if w[0] == "ip" else "ipv6",
                        "protocol": w[2], "name": w[4]})
        elif w[:1] == ["fib-policy"] and len(w) == 3:
            att.append({"kind": "fib-policy", "family": "",
                        "protocol": w[1], "name": w[2]})
        elif w[:1] in (["route-map"], ["router"], ["rpki"], ["vrf"],
                       ["interface"]) \
                or w[:2] in (["ip", "prefix-list"], ["ipv6", "prefix-list"],
                             ["ip", "route"], ["ipv6", "route"]) \
                or (w[:1] == ["bgp"] and len(w) > 1
                    and w[1].endswith("-list")) \
                or w[:2] == ["bgp", "as-path"] or box_owned(line):
            continue
        else:
            other.append(line)
    return {"attachments": att, "other": other}


def vrfs_typed(cfg):
    """The Linux VRF devices (#540): name and table."""
    return [{"name": n, "table": t} for n, t in sorted(cfg.vrf_devices.items())]


def vrrp_typed(cfg):
    """The virtual routers (#7), per interface and VRID."""
    return [{"interface": ifname, "vrid": vrid,
             "addresses": list(vr["addresses"]), "priority": vr["priority"],
             "track": [{"interface": t, "decrement": d}
                       for t, d in sorted(vr["track"].items())]}
            for ifname in sorted(cfg.vrrp)
            for vrid, vr in sorted(cfg.vrrp[ifname].items())]


def bundles_typed(cfg):
    """The LACP bundles (#455): members, LACP mode, rate and hash."""
    return [{"name": n, "members": list(b["members"]), "lacp": b["lacp"],
             "rate": b["rate"], "hash": b["hash"]}
            for n, b in sorted(cfg.bundles.items())]


#: The system fields a typed edit may set, flattened: a nested setting is
#: named by its block and key, `ssh.port`. Secrets are not among them - an
#: SNMP community, a password or a key is set as a line, never typed here.
SYSTEM_FIELDS = ("hostname", "dns_servers", "ssh.port", "ssh.listen_address",
                 "ssh.permit_root_login", "ssh.password_authentication",
                 "api.enabled", "api.listen_address", "api.port",
                 "snmp.enabled", "snmp.listen_address", "snmp.port",
                 "snmp.enterprise_number")


def _get(d, field):
    """d's value for a flattened field, `ssh.port` -> d["ssh"]["port"]."""
    for part in field.split("."):
        d = (d or {}).get(part)
    return d


def _yes(v):
    return "yes" if v else "no"


def system_lines(current, desired, fields=()):
    """
    The VTY lines that take the system settings from `current` (a
    system_typed dict) to `desired` for the fields named - only what
    differs, at the configuration level. An empty listen-address is the
    statement removed; a zero port or number is left alone, as no value.
    """
    out = []
    for f in _mask(fields, SYSTEM_FIELDS):
        now, want = _get(current, f), _get(desired, f)
        if f == "dns_servers":
            now, want = list(now or []), list(want or [])
            out += [f"no dns server {a}" for a in now if a not in want]
            out += [f"dns server {a}" for a in want if a not in now]
            continue
        if now == want:
            continue
        block, _, key = f.partition(".")
        word = key.replace("_", "-")
        if f == "hostname":
            out.append(f"hostname {want}" if want else "no hostname")
        elif key in ("enabled", "password_authentication"):
            out.append(f"{block} {word} {_yes(want)}")
        elif key == "listen_address":
            out.append(f"{block} {word} {want}" if want
                       else f"no {block} {word}")
        elif key in ("port", "enterprise_number"):
            if want:
                out.append(f"{block} {word} {want}")
        else:
            out.append(f"{block} {word} {want}")
    return out


def vrf_lines(current, desired):
    """`vrf <name> table <id>` for a VRF device (#540), when it differs."""
    name, table = desired.get("name") or "", desired.get("table") or 0
    if not name or not table:
        raise ValueError("vrf: give the device's name and its table")
    if current and current.get("table") == table:
        return []
    return [f"vrf {name} table {table}"]


def cgnat_globals_typed(cfg):
    """
    The `nat` > `cgnat` statements that are not a pool, an interface's role
    or the log export: the session timeouts, the block hold-down, where
    identities are read from (#21; a RADIUS secret is never read, only
    whether one is set), the session table's sizing and the CGNAT's memory.
    A statement no field takes is in `other`, verbatim.
    """
    out = {"timeouts": [], "block_holddown": 0, "identity_source": "",
           "identity_path": "", "identity_radius_port": 0,
           "identity_radius_secret_set": False, "session_table_buckets": 0,
           "session_table_memory_mb": 0, "memory_gb": cfg.cgnat_memory_gb,
           "other": []}
    timeouts = {}
    for g in cfg.globals:
        w = g.split()
        w = w[1:] if w[:1] == ["cgnat"] else w
        if w[:1] == ["timeout"] and len(w) == 3:
            timeouts[w[1]] = w[2]
        elif w[:1] == ["block-holddown"] and len(w) == 2:
            out["block_holddown"] = _int(w[1])
        elif w[:1] == ["identity"] and len(w) > 1:
            out["identity_source"] = w[1]
            if w[1] in ("file", "dhcp-leases") and len(w) > 2:
                out["identity_path"] = w[2]
            elif w[1] == "radius":
                out["identity_radius_secret_set"] = "secret" in w
                if "port" in w and w.index("port") + 1 < len(w):
                    out["identity_radius_port"] = _int(w[w.index("port") + 1])
        elif w[:1] == ["session-table"]:
            for k, field in (("buckets", "session_table_buckets"),
                             ("memory", "session_table_memory_mb")):
                if k in w and w.index(k) + 1 < len(w):
                    out[field] = _int(w[w.index(k) + 1])
            if len(w) == 2 and is_number(w[1]):
                out["session_table_buckets"] = _int(w[1])
        elif w[:1] == ["logging"]:
            continue              # the log export's own area
        else:
            out["other"].append(g)
    out["timeouts"] = _kv(timeouts)
    return out


SYSTEM_READERS = {"system": system_typed, "users": users_typed,
                  "snmp_users": snmp_users_typed, "policy": policy_typed,
                  "route_maps": route_maps_typed,
                  "frr_lists": frr_lists_typed, "rpki": rpki_typed,
                  "routing_attachments": routing_attachments_typed,
                  "vrfs": vrfs_typed, "vrrp": vrrp_typed,
                  "bundles": bundles_typed,
                  "cgnat_globals": cgnat_globals_typed}


def system_areas_typed(cfg, areas):
    """The phase-4 areas asked for, of Config `cfg`."""
    return {a: SYSTEM_READERS[a](cfg) for a in SYSTEM_AREAS if a in areas}


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
                    "pref64",
                    "vrf", "vlan", "description", "ipv4", "ipv6", "shutdown",
                    "mtu", "urpf", "cgnat_role")

#: Pool fields a typed edit may set, in the order their lines go.
POOL_FIELDS = ("prefix_inside", "prefix_outside", "block_size", "ports",
               "policy", "mode", "reserve", "vrf", "outside_vrf", "timeouts",
               "max_blocks", "initial_allocation", "block_stride",
               "bypass_source", "bypass_destination", "utilisation_alarm",
               "max_sessions", "max_session_rate", "tcp_mss", "filter",
               "algs", "forwards")
#: A NAT64 pool's (#572): a CGNAT pool's, less the bypass it never takes,
#: and with the two statements that make it NAT64.
NAT64_POOL_FIELDS = tuple(f for f in POOL_FIELDS
                          if f not in ("bypass_source", "bypass_destination")
                          ) + ("subscriber_length", "nat64_prefix")

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
        if f == "pref64":
            out += _list_lines("ipv6 nd nat64", now, list(want or []))
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
            "mac_address": "", "urpf": "", "router_advertisements": False,
            "pref64": []}


def pool_typed_blank(name):
    """A pool as the VTY creates it: Pool()'s own defaults."""
    from config import Pool
    return pool_typed(Pool(name))


def _forward_line(f):
    return (f"forward {f['protocol']} {int(f['public_port'])} to "
            f"{f['private_address']} {int(f['private_port'])}")


def pool_lines(current, desired, fields=(), known=POOL_FIELDS):
    """
    The VTY lines, typed under `nat` > `cgnat` > `pool <name>` - or, with
    `known` NAT64_POOL_FIELDS, under `nat` > `nat64` (#572) - that take pool
    `current` (pool_typed's shape, None for a new one) to `desired`.
    """
    cur = current or pool_typed_blank(desired["name"])
    out = []
    for f in _mask(fields, known):
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
        elif f == "nat64_prefix":
            out.append(f"nat64-prefix {want}" if want else "no nat64-prefix")
        else:
            word = {"initial_allocation": "initial-allocation",
                    "subscriber_length": "subscriber-length",
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


def pool_path(name, level="cgnat"):
    return ["nat", level, f"pool {name}"]


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
    if area == "nat64":
        return ["nat", "nat64"], [f"no pool {name}"]
    if area == "logging":
        if transport not in TRANSPORTS:
            raise ValueError(f"logging: name the transport to remove: "
                             f"{', '.join(TRANSPORTS)}")
        return list(LOGGING_PATH), [f"logging {transport} disable"]
    if area == "vrfs":
        if not name:
            raise ValueError("vrfs: name the VRF device to remove")
        return [], [f"no vrf {name} table"]
    raise ValueError(f"unknown area {area!r}: the removable areas are "
                     f"interfaces, pools, nat64, logging, static, bgp, vrfs")


# ------------------------------------------------------------------ schema

_RANGE = re.compile(r"^<(-?\d+)-(\d+)>$")


#: An operator VRF's FRR block, as FRR takes and prints it: the static
#: routes of that VRF. No VTY grammar holds it - the VTY passes it to FRR -
#: so it is written here for GetSchema and the coverage document.
VRF_FRR_BLOCK = {
    "ip": ("IPv4 in this VRF", {
        "route": ("a static route of this VRF",
                  "<A.B.C.D/M> <next-hop>... [tag <1-4294967295>] "
                  "[<1-255>]")}),
    "ipv6": ("IPv6 in this VRF", {
        "route": ("a static route of this VRF",
                  "<X:X::X:X/M> <next-hop>... [tag <1-4294967295>] "
                  "[<1-255>]")}),
}


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
        # #572: NAT64's level and its pool, the same pool grammar.
        ("nat64", "nat > nat64", '["nat","nat64"]', P.TREE["nat64"], cg,
         ["nat", "nat64"]),
        ("nat64 pool", "nat > nat64 > pool <name>",
         '["nat","nat64","pool <name>"]', P.TREE["pool"], cg,
         ["nat", "nat64", "pool <name>"]),
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
        # #559 phase 3: the `bfd` block and its two kinds of entry.
        ("bfd", "bfd", '["bfd"]', P.BFD_BLOCK, adm, ["bfd"]),
        ("bfd peer", "bfd > peer <address> ...", '["bfd","peer <address>"]',
         P.BFD_PEER, adm, ["bfd", "peer <address>"]),
        ("bfd profile", "bfd > profile <name>", '["bfd","profile <name>"]',
         P.BFD_PROFILE, adm, ["bfd", "profile <name>"]),
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
        # An operator VRF's own FRR block (#558 phase 4): its static
        # routes, which phase 3 reads as `static` with their `vrf`. The
        # VTY hands the block to FRR as it is; this is FRR's syntax.
        ("vrf", "vrf <name>", '["vrf <name>"]', VRF_FRR_BLOCK, adm,
         ["vrf <name>"]),
    ]


#: The modes GetConfig/SetConfig read and write as fields (#558, phase 2),
#: and which of their statements: None is every one.
TYPED_MODES = {"interface (port)": ("interfaces", None),
               "interface (bundle)": ("interfaces", None),
               "pool": ("pools", None),
               "nat64 pool": ("nat64", None),
               "cgnat": ("logging", ("logging",)),
               # Phase 4: SetConfig's `system` and `vrf_device`. `snmp` is
               # left out on purpose: its community and trap-host carry a
               # secret and are written as lines (its other settings are
               # typed, which the first word alone cannot say).
               "config": ("system", ("hostname", "dns", "ssh", "api",
                                     "vrf")),
               # #559 phase 3: SetConfig's `bfd_peer` and `bfd_profile`.
               "bfd": ("bfd", ("peer", "profile")),
               "bfd peer": ("bfd", None), "bfd profile": ("bfd", None)}

#: The modes GetConfig READS as fields though they are written as lines
#: (#558 phases 3 and 4): their statements are `read_typed`, and `typed`
#: only where TYPED_MODES says a SetConfig writes them too.
READ_MODES = {"config": "system", "router static": "static",
              "router bgp": "bgp",
              "bgp neighbor": "bgp", "address-family": "bgp",
              "address-family neighbor": "bgp", "router ospf": "ospf",
              "router ospf6": "ospf6", "router isis": "isis",
              "system": "system", "system memory": "system",
              "system cpu": "system", "system dataplane": "system",
              "user": "users", "snmp-user": "snmp_users",
              "policy-options": "policy", "prefix-list": "policy",
              "policy-statement": "policy", "term": "policy",
              "route-map": "route_maps", "rpki": "rpki", "vrf": "static"}


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


def split_alternatives(token):
    """
    A grammar token's `|` alternatives, the `|` inside a `<...>` kept:
    `<1-4294967295>|internal|external` -> ["<1-4294967295>", "internal",
    "external"]; `<A.B.C.D|X:X::X:X>` is one alternative.
    """
    out, cur, depth = [], "", 0
    for ch in token:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [a for a in out if a]


def _is_placeholder(alt):
    """An alternative that is not one literal word: `<n>`, and patterns
    built around one too - `+<n>`, `soo:<asn>:<value>`, `<asn>...`."""
    return "<" in alt


def _clean(alt):
    """One `<...>` placeholder, or one literal word: an alternative a client
    can be given as a kind or a choice value. A compound pattern -
    `+<n>`, `soo:<asn>:<value>` - is not, and keeps its token whole."""
    if alt.startswith("<") and alt.endswith(">"):
        return alt.count("<") == 1
    return not any(c in alt for c in "<>:")


def _alternation(t):
    """The alternatives of token `t` when every one is clean, else None:
    the token is then one pattern, given whole."""
    alts = split_alternatives(t)
    if len(alts) < 2 or not all(_clean(a) for a in alts):
        return None
    return alts


def _kind_of(t):
    """The kind of one alternative that is a single placeholder or word."""
    m = _RANGE.match(t)
    if m:
        return "integer", int(m.group(1)), int(m.group(2))
    kinds = {"<A.B.C.D/M>": "ipv4_prefix", "<A.B.C.D>": "ipv4_address",
             "<X:X::X:X/M>": "ipv6_prefix", "<X:X::X:X>": "ipv6_address",
             "<xx:xx:xx:xx:xx:xx>": "mac"}
    if t in kinds:
        return kinds[t], 0, 0
    if t in ("<A.B.C.D|X:X::X:X>", "<collector>", "<source>", "<address>",
             "<private-ip>", "<next-hop>", "<neighbour>", "<router-id>",
             "<host>"):
        return "address", 0, 0
    if t in ("<prefix>", "<A.B.C.D/M|X:X::X:X/M>", "<network>"):
        return "prefix", 0, 0
    if t in ("<text>", "<line>", "<rest>"):
        return "text", 0, 0
    if t in ("<n>", "<ports>", "<seconds>", "<port>", "<GB>", "<MB>",
             "<asn>", "<seq>", "<number>", "<metric>", "<value>", "<delay>",
             "<hold>", "<keepalive>", "<init>", "<max>", "<minutes>",
             "<percent>"):
        return "integer", 0, 0
    if _is_placeholder(t):
        return "word", 0, 0
    return "keyword", 0, 0


def argument_of(token, sources=None):
    """
    One word of a VTY hint as a schema argument: {"name", "kind", "min",
    "max", "choices", "optional", "source"}. The kinds are integer,
    ipv4_prefix, ipv4_address, ipv6_prefix, ipv6_address, address, prefix,
    mac, choice, word, text, keyword; `source` names where the VTY completes
    its values from, when it does.

    A token of alternatives (`inside|outside`, `<on|off>`) is a choice of
    its literal values. One that mixes a placeholder with literal words
    (`<1-4294967295>|internal|external`) is read here as the choice of the
    literals with the integer's range beside it - hint_forms() makes it two
    forms instead, which is what GetSchema answers (#558 phase 4: 0.149.22
    stripped the angle brackets off the whole token and answered the value
    "1-4294967295>").
    """
    opt = token.startswith("[") and token.endswith("]")
    t = token.strip("[]").rstrip(",")
    many = t.endswith("...")
    arg = {"name": t, "kind": "word", "min": 0, "max": 0, "choices": [],
           "optional": opt, "source": (sources or {}).get(t, ""),
           "many": many}
    if many:
        t = t[:-3]
    if t.startswith("<") and t.endswith(">") and "|" in t \
            and _kind_of(t)[0] != "word":
        kind, lo, hi = _kind_of(t)     # an address or prefix of either family
        arg.update(kind=kind, min=lo, max=hi)
        return arg
    alts = _alternation(t)
    if alts is None and t.startswith("<") and t.endswith(">") and "|" in t \
            and t.count("<") == 1:
        alts = t[1:-1].split("|")      # `<on|off>`: a choice in one bracket
    if alts:
        lits = [a for a in alts if not _is_placeholder(a)]
        arg.update(kind="choice", choices=lits)
        for a in alts:
            k, lo, hi = _kind_of(a)
            if _is_placeholder(a) and k == "integer":
                arg.update(min=lo, max=hi)
        return arg
    kind, lo, hi = _kind_of(t)
    arg.update(kind=kind, min=lo, max=hi)
    return arg


def _hint_tokens(hint):
    """
    (token, optional) of a hint, `[...]` groups optional, separators out.
    A token ending in a comma ends the grammar: what follows is prose for
    the reader (`<interface>, or a name that creates one`), and 0.149.22
    read its words as required keywords, so no `interface <name>` line
    matched its own statement.
    """
    out, opt = [], 0
    toks = hint.replace("[", " [ ").replace("]", " ] ").split()
    for k, tok in enumerate(toks):
        if tok.endswith(",") and tok != "," and k + 1 < len(toks) \
                and not toks[k + 1].startswith(("<", "[")):
            out.append((tok.rstrip(","), opt > 0))
            break
        if tok == "[":
            opt += 1
            continue
        if tok == "]":
            opt = max(0, opt - 1)
            continue
        if tok in ("|", "or", "...", ","):
            continue
        out.append((tok, opt > 0))
    return out


def hint_arguments(hint, sources=None):
    """A hint string as its arguments, `[...]` groups as optional ones."""
    if not hint:
        return []
    out = []
    for tok, opt in _hint_tokens(hint):
        a = argument_of(tok, sources)
        a["optional"] = a["optional"] or opt
        out.append(a)
    return out


def _expand(tokens, sources):
    """
    The forms one alternative of a hint takes: a token that mixes a
    placeholder with literal words gives one form per placeholder and one
    for the literals, so a client never has to read `<1-n>|word` itself.
    """
    forms = [[]]
    for tok, opt in tokens:
        t = tok.strip("[]").rstrip(",")
        alts = _alternation(t) or []
        mixed = any(_is_placeholder(a) for a in alts)
        if not mixed:
            a = argument_of(tok, sources)
            a["optional"] = a["optional"] or opt
            forms = [f + [a] for f in forms]
            continue
        lits = [a for a in alts if not _is_placeholder(a)]
        variants = []
        for alt in alts:
            if _is_placeholder(alt):
                a = argument_of(alt, sources)
                a["optional"] = opt
                variants.append(a)
        if lits:
            variants.append({"name": "|".join(lits), "kind": "choice",
                             "min": 0, "max": 0, "choices": lits,
                             "optional": opt, "source": "", "many": False})
        forms = [f + [v] for f in forms for v in variants]
    return forms


def group_keywords(form):
    """
    Form `form` with each keyword-led part marked: the keyword and the values
    right after it that share its optional/required standing get a `group`
    (1, 2, ...) and are `unordered` - FRR prints such parts in an order of
    its own (`logging syslog <c> port 1601 enterprise 67033 ...` against the
    grammar's `enterprise <n> [port <n>]`). The rest is positional, group 0,
    in order: `[seq <n>] permit|deny <prefix>` is the group `seq <n>`, then
    the positional choice and prefix.
    """
    group, lead_opt, in_group = 0, None, False
    for a in form:
        if a["kind"] == "keyword":
            group += 1
            lead_opt, in_group = a["optional"], True
            a["group"], a["unordered"] = group, True
        elif in_group and a["optional"] == lead_opt:
            a["group"], a["unordered"] = group, True
        else:
            in_group = False
            a["group"], a["unordered"] = 0, False
    return form


def hint_forms(hint, sources=None):
    """Every form a hint allows: its top-level ` | ` alternatives, each
    with its mixed alternations expanded and its keyword-led parts marked."""
    if not hint:
        return [[]]
    out = []
    for part in hint.split(" | "):
        out += [group_keywords(f) for f in _expand(_hint_tokens(part),
                                                   sources)]
    return out


#: Words every level offers that are the session's, or are read elsewhere.
_SESSION = ("exit", "top", "up", "end", "show", "commit", "rollback", "no",
            "exit-address-family", "save")


def key_words(keys, sources=None):
    """
    (words, arguments) of a statement's grammar keys - `["neighbor
    <address>", "remote-as"]`: the literal words, and each placeholder an
    argument with the number of words before it as its `position`. 0.149.22
    put the placeholders among the words (#558 phase 4).
    """
    words, args = [], []
    for tok in " ".join(keys).split():
        if _is_placeholder(tok) or "|" in tok:
            a = argument_of(tok, sources)
            a.update(position=len(words), group=0, unordered=False)
            args.append(a)
        else:
            words.append(tok)
    return words, args


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
            words, lead = key_words(prefix + [key], sources)
            forms = []
            for form in hint_forms(hint, sources):
                for a in form:
                    a["position"] = len(words)
                forms.append([dict(x) for x in lead] + form)
            out.append({"words": words, "help": help_, "forms": forms,
                        "first": (prefix + [key])[0].split()[0],
                        "repeatable": "may repeat" in (help_ or "")})


def takes_rest(arg):
    """Whether argument `arg` takes every token left on the line."""
    return arg["kind"] == "text" or bool(arg.get("many"))


def _value_ok(arg, tok):
    """Whether one token can be argument `arg`'s value."""
    kind = arg["kind"]
    if kind == "keyword":
        return tok == arg["name"]
    if kind == "choice":
        return tok in arg["choices"]
    if kind == "integer":
        if not is_number(tok.lstrip("+-")):
            return False
        return not arg["max"] or arg["min"] <= int(tok) <= arg["max"]
    if kind in ("ipv4_prefix", "ipv4_address"):
        return "." in tok and ":" not in tok
    if kind in ("ipv6_prefix", "ipv6_address"):
        return ":" in tok
    return bool(tok)


def form_matches(tokens, words, form):
    """
    Whether `tokens` (a line's words, `no` already taken off) are one use of
    a statement with `words` and arguments `form`: the words in order with
    each argument at its `position` among them; after them, a token that is
    an unused group's keyword takes that group - in ANY order - and any
    other fills the next positional argument, an optional one that does not
    fit being skipped. A `text` argument, or one that takes `many` values,
    takes the rest of the line. Every required argument and group is there.
    """
    i = 0
    lead = [a for a in form if a.get("position", len(words)) < len(words)]
    trail = [a for a in form if a.get("position", len(words)) >= len(words)]
    for w_i, w in enumerate(words):
        for a in [x for x in lead if x["position"] == w_i]:
            if i >= len(tokens) or not _value_ok(a, tokens[i]):
                return False
            i += 1
        if i >= len(tokens) or tokens[i] != w:
            return False
        i += 1
    positional = [a for a in trail if not a.get("unordered")]
    groups = {}
    for a in trail:
        if a.get("unordered"):
            groups.setdefault(a["group"], []).append(a)
    seen, p = set(), 0
    while i < len(tokens):
        g = next((g for g, m in groups.items()
                  if g not in seen and m[0]["name"] == tokens[i]), None)
        if g is not None:
            j = i + 1
            for a in groups[g][1:]:
                if takes_rest(a):
                    j = len(tokens)
                    break
                if j >= len(tokens) or not _value_ok(a, tokens[j]):
                    return False
                j += 1
            seen.add(g)
            i = j
            continue
        while p < len(positional) and not takes_rest(positional[p]) \
                and not _value_ok(positional[p], tokens[i]) \
                and positional[p]["optional"]:
            p += 1
        if p >= len(positional):
            return False
        a = positional[p]
        if takes_rest(a):
            return all(g in seen or m[0]["optional"]
                       for g, m in groups.items())
        if not _value_ok(a, tokens[i]):
            return False
        p, i = p + 1, i + 1
    return (all(a["optional"] for a in positional[p:])
            and all(g in seen or m[0]["optional"] for g, m in groups.items()))


def statement_matches(line, st):
    """Whether config line `line` is a use - or the `no` - of schema
    statement `st`: some form matches its words."""
    tokens = line.split()
    if tokens[:1] == ["no"] and st.get("removable"):
        tokens = tokens[1:]
        return any(form_matches(tokens, st["words"], f) or
                   form_matches(tokens, st["words"],
                                [a for a in f if a.get("position", 0)
                                 < len(st["words"])])
                   for f in st["forms"])
    return any(form_matches(tokens, st["words"], f) for f in st["forms"])


def _path_matches(pattern, headers):
    """Whether block headers `headers` are a mode's EditRequest.path
    `pattern` - `["router bgp <asn>"]` against `["router bgp 65000"]`."""
    if len(pattern) != len(headers):
        return False
    for p, h in zip(pattern, headers):
        pw, hw = p.split(), h.split()
        # `bond<n>` is a placeholder too: a bundle's path is
        # `interface bond<n>`, and its header `interface ae0`.
        lit = [w for w in pw if "<" not in w and "|" not in w]
        if hw[:len(lit)] != lit[:len(hw)] and not all(
                w in hw for w in lit):
            return False
        if pw and hw and pw[0] != hw[0]:
            return False
    return True


def unmatched_lines(sch, text):
    """
    The lines of configuration text `text` (as `show running-config` prints
    it, indented by block) that no statement of schema `sch` describes: each
    line is tried against the modes whose path its block headers are, its
    `<- not checked` mark and the box-owned lines left aside. Empty is what
    a client building on GetSchema needs - every line of a running box
    matches a statement, order-free parts included.
    """
    stack, misses = [], []
    for raw in text.splitlines():
        line = raw.split("<- not checked")[0].rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("!") or stripped in (
                "exit", "exit-vrf", "exit-address-family", "end"):
            if stripped in ("exit", "exit-vrf", "exit-address-family"):
                depth = len(line) - len(stripped)
                stack = [(d, h) for d, h in stack if d < depth]
            continue
        depth = len(line) - len(stripped)
        stack = [(d, h) for d, h in stack if d < depth]
        headers = [h for _d, h in stack]
        if box_owned(stripped, any(h.startswith("vrf prisma-")
                                   for h in headers)):
            stack.append((depth, stripped))
            continue
        modes = [m for m in sch if _path_matches(m["path"], headers)]
        if not any(statement_matches(stripped, st) for m in modes
                   for st in m["statements"]):
            misses.append(("/".join(headers), stripped))
        stack.append((depth, stripped))
    return misses


def _negations(node):
    neg = node.get("no")
    if not isinstance(neg, tuple) or not isinstance(neg[1], dict):
        return set()
    return set(neg[1])


#: Statements the VTY takes that its `?` tree does not list, on purpose -
#: `vrf <name> table <id>` (#540) is left out so the tree's "not checked"
#: mark does not claim FRR's `vrf <name>` block - but that GetSchema must
#: still describe, or a running box's line matches nothing (#558 phase 4).
#: {mode: [(keys, hint, help, removable)]}.
EXTRA_STATEMENTS = {
    "config": [(["vrf <name>", "table"], "<1-4294967295>",
                "a VRF device on the host, on the table its interfaces' "
                "`vrf <n>` use (#540)", True),
               # A route-map's header is how its block is entered: the
               # route-map mode's own path, a statement at this level too.
               (["route-map <name>"], "permit|deny <1-65535>",
                "a route-map entry, entered as a block", True),
               # An operator VRF's FRR block, entered as `vrf <name>`.
               (["vrf <name>"], "", "a VRF's own FRR block, entered", True)],
}


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
        extra_removable = set()
        for keys, hint, help_, removable in EXTRA_STATEMENTS.get(mode, ()):
            _walk({" ".join(keys): (help_, hint)}, [], stmts, sources)
            if removable:
                extra_removable.add(id(stmts[-1]))
        neg = _negations(node)
        area, only = TYPED_MODES.get(mode, ("", None))
        read_area = READ_MODES.get(mode, area)
        for st in stmts:
            first = st.pop("first")
            st["removable"] = id(st) in extra_removable or first in neg or (
                mode == "cgnat" and st["words"][:1] == ["logging"]
                and len(st["words"]) > 1 and st["words"][1] in TRANSPORTS)
            st["no_words"] = (["no"] + st["words"]
                              if id(st) in extra_removable or first in neg
                              else [])
            st["typed"] = bool(area) and (only is None or first in only)
            st["read_typed"] = st["typed"] or bool(read_area)
        out.append({"mode": mode, "path": list(path), "enter": enter,
                    "roles": roles, "typed": read_area or area,
                    "statements": stmts})
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
    if area in ("pools", "nat64"):
        name = obj.get("name") or ""
        if not name:
            raise ValueError("pool: name the pool")
        level = "nat64" if area == "nat64" else "cgnat"
        held = cfg.pools.get(name)
        # One name, one pool, across both levels (#572), refused here with
        # the VTY's sentence rather than staged under the wrong level.
        if held is not None and getattr(held, "level", "cgnat") != level:
            raise ValueError(f"pool '{name}' is a pool under "
                             f"'{held.level}'")
        cur = pool_typed(held) if held is not None else None
        return pool_path(name, level), pool_lines(
            cur, obj, fields,
            NAT64_POOL_FIELDS if area == "nat64" else POOL_FIELDS)
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
    if area == "system":
        return [], system_lines(system_typed(cfg), obj, fields)
    if area == "vrfs":
        name = obj.get("name") or ""
        cur = ({"name": name, "table": cfg.vrf_devices[name]}
               if name in cfg.vrf_devices else None)
        return [], vrf_lines(cur, obj)
    if area == "bfd_peers":
        cur = next((p for p in bfd_typed(cfg)["peers"]
                    if same_bfd_peer(p, obj)), None)
        return list(BFD_PATH), bfd_peer_lines(cur, obj, fields)
    if area == "bfd_profiles":
        cur = next((p for p in bfd_typed(cfg)["profiles"]
                    if p["name"] == obj.get("name")), None)
        return list(BFD_PATH), bfd_profile_lines(cur, obj, fields)
    raise ValueError(f"unknown area {area!r}: the editable areas are "
                     f"interfaces, pools, nat64, logging, static, bgp, "
                     f"system, vrfs, bfd_peers, bfd_profiles")


def names_for(cfg, area, known_interfaces=()):
    """What `<name>` may be for a typed area: the configured objects, and
    for interfaces also those the dataplane has."""
    if area == "interfaces":
        return sorted(set(cfg.interface_names()) | set(known_interfaces))
    if area == "pools":
        return sorted(n for n, p in cfg.pools.items()
                      if getattr(p, "level", "cgnat") == "cgnat")
    if area == "nat64":
        return sorted(n for n, p in cfg.pools.items()
                      if getattr(p, "level", "cgnat") == "nat64")
    return []
