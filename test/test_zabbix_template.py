"""
appliance/prisma-routing-zabbix.json.in (#514): the Zabbix 7.0 template,
held to the program that writes it and to the box it monitors.

Each shown red by a mutation:

  * the file is tools/zabbix-template.py's output, byte for byte (mutation:
    edit a name in the file alone);
  * every UUID is a UUIDv4, as Zabbix's import requires, and none repeats
    (mutation: the uuid5 the first cut used - refused by a real Zabbix 7.0 with
    "UUIDv4 is expected");
  * every OID polled is an object a module defines, readable, and inside the
    view snmpd.conf grants (mutation: ifHCInOctets one column off, 1.3.6.1.2.1.
    31.1.1.1.7, which is ifHCInUcastPkts - it resolves, to the wrong object,
    and is caught by the name check below);
  * the interface columns are ones the agent serves (mutation: ifInUnknownProtos,
    .2.2.1.15, which IF-MIB defines and the agent does not);
  * every trigger names items the template has and macros it defines, every
    value map an item names exists, and every LLD macro a prototype uses is
    one its rule discovers (mutation: rename net.if.status in one place);
  * @PEN@ appears once, as {$PRISMA.PEN}'s default, which is what
    `show snmp zabbix-template` fills in.

Imported into a real Zabbix 7.0 and polled against a lab box in #514's
branch; that is the evidence the schema is Zabbix's, which nothing here can
substitute for.
"""
import glob
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
from system import SNMP_VIEW, PRODUCT_PEN  # noqa: E402

FILE = os.path.join(REPO, "appliance", "prisma-routing-zabbix.json.in")
GEN = os.path.join(REPO, "tools", "zabbix-template.py")
fail = 0


def check(c, msg):
    global fail
    print(("  [ ok ] " if c else "  FAIL: ") + msg)
    fail += 0 if c else 1


# The OID arcs a module's definitions hang from, where the module that
# defines them is not in test/mibs.
ROOTS = {"iso": "1", "org": "1.3", "dod": "1.3.6", "internet": "1.3.6.1",
         "mgmt": "1.3.6.1.2", "mib-2": "1.3.6.1.2.1", "experimental": "1.3.6.1.3",
         "private": "1.3.6.1.4", "enterprises": "1.3.6.1.4.1",
         "ip": "1.3.6.1.2.1.4"}


def strip_asn1(text):
    """
    `text` with its strings emptied and its comments removed, in one pass.
    Two regexes do not do it: a string holding "--" loses its end to the
    comment rule, and a comment holding a quote - ISIS-MIB has one - flips
    every string after it, and the definitions they then swallow resolve to
    nothing.
    """
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            if c == '"':
                in_str = False
                out.append(c)
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def load_mibs(paths):
    """{name: (parent, [sub-ids], access)} for every object in `paths`."""
    defs = {}
    for path in paths:
        text = open(path, errors="replace").read().replace("@PEN@", PRODUCT_PEN)
        text = strip_asn1(text)
        for m in re.finditer(
                r"(?m)^[ \t]*(?!SYNTAX\b|IMPORTS\b)([a-zA-Z][\w-]*)[ \t]+"
                r"(OBJECT-TYPE|OBJECT IDENTIFIER|MODULE-IDENTITY|OBJECT-IDENTITY|NOTIFICATION-TYPE)"
                r"(.*?)::=\s*\{\s*([^}]*)\}", text, re.S):
            name, _, body, parent = m.groups()
            parts = parent.split()
            if not parts:
                continue
            subs = [x for p in parts[1:]
                    for x in re.findall(r"^(\d+)$|\((\d+)\)", p)[0] if x] \
                if len(parts) > 1 else []
            acc = re.search(r"MAX-ACCESS\s+([\w-]+)", body)
            defs.setdefault(name, (re.sub(r"\(\d+\)$", "", parts[0]), subs,
                                   acc.group(1) if acc else None))
    return defs


def resolve(defs, name):
    """The dotted OID of `name`, or None."""
    if name in ROOTS:
        return ROOTS[name]
    if name not in defs:
        return None
    base = resolve(defs, defs[name][0])
    return None if base is None else ".".join([base] + defs[name][1])


def columns_of(snmp_oid):
    """
    The objects an SNMP OID field reads: a discovery[] names columns, which
    it walks; an item names an instance, whose object is column() of it.
    """
    s = snmp_oid.replace("{$PRISMA.PEN}", PRODUCT_PEN)
    m = re.fullmatch(r"discovery\[(.*)\]", s)
    if m:
        return [p for p in m.group(1).split(",") if re.fullmatch(r"[0-9.]+", p)]
    return [column(s)] if re.fullmatch(r"[0-9.]+(\.\{#SNMPINDEX\})?", s) else []


def column(oid):
    """
    The object an OID polls: a scalar's .0, a discovered row's index, or the
    one fixed row the template reads (laLoad.1, the one-minute average),
    taken off.
    """
    if oid.endswith(".{#SNMPINDEX}"):
        return oid[:-len(".{#SNMPINDEX}")]
    return oid.rsplit(".", 1)[0]


def walk(tpl):
    """(kind, object, rule or None) for every item, prototype and rule."""
    for i in tpl["items"]:
        yield "item", i, None
    for r in tpl["discovery_rules"]:
        yield "rule", r, None
        for p in r["item_prototypes"]:
            yield "prototype", p, r


# ------------------------------------------------------------------ the file
print("=== the file is the generator's output ===")
out = subprocess.run([sys.executable, GEN], capture_output=True, text=True)
shipped = open(FILE).read()
check(out.returncode == 0 and out.stdout == shipped,
      "appliance/prisma-routing-zabbix.json.in is `python3 tools/zabbix-template.py`'s "
      "output: regenerate it rather than editing it")
doc = json.loads(shipped)["zabbix_export"]
check(doc["version"] == "7.0", f"a Zabbix 7.0 export: {doc['version']}")
tpl = doc["templates"][0]

# ------------------------------------------------------------------ UUIDs
print("\n=== UUIDs: v4, and each once ===")
uuids = []


def collect(o):
    """Every "uuid" value anywhere in `o`."""
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "uuid":
                uuids.append(v)
            else:
                collect(v)
    elif isinstance(o, list):
        for v in o:
            collect(v)


collect(doc)
bad = [u for u in uuids if not re.fullmatch(r"[0-9a-f]{12}4[0-9a-f]{3}[89ab][0-9a-f]{15}", u)]
check(not bad, f"{len(uuids)} UUIDs, every one a UUIDv4 in Zabbix's 32-hex form: {bad[:3]}")
check(len(set(uuids)) == len(uuids),
      f"none repeats: {sorted(u for u in set(uuids) if uuids.count(u) > 1)[:3]}")

# ------------------------------------------------------------------ OIDs
print("\n=== every OID is an object the box serves ===")
mibs = sorted(glob.glob(os.path.join(REPO, "test", "mibs", "*.txt")))
mibs.append(os.path.join(REPO, "appliance", "PRISMA-ROUTING-MIB.in"))
defs = load_mibs(mibs)
by_oid = {resolve(defs, n): n for n in defs if resolve(defs, n)}
# BGP4V2-MIB is an IETF draft and no module of it is shipped; these are the
# peer table's columns as FRR's bgpd defines them (bgpd/bgp_snmp_bgp4v2.h:
# BGP4V2_PEER_REMOTE_AS 10, _ADMIN_STATUS 12, _STATE 13, _DESCRIPTION 14),
# which lab/routing.sh reads from a live bgpd.
BGP4V2 = {"1.3.6.1.3.5.1.1.2.1.10": "bgp4V2PeerRemoteAs",
          "1.3.6.1.3.5.1.1.2.1.12": "bgp4V2PeerAdminStatus",
          "1.3.6.1.3.5.1.1.2.1.13": "bgp4V2PeerState",
          "1.3.6.1.3.5.1.1.2.1.14": "bgp4V2PeerDescription"}
view = [v.lstrip(".") for v in SNMP_VIEW] + [f"1.3.6.1.4.1.{PRODUCT_PEN}"]
polled = []
for kind, o, _ in walk(tpl):
    for col in columns_of(o.get("snmp_oid", "")):
        polled.append((o["key"], col))
unknown, unreadable, outside = [], [], []
for key, col in polled:
    name = BGP4V2.get(col) or by_oid.get(col)
    if not name:
        unknown.append((key, col))
    elif col not in BGP4V2 and defs[name][2] not in ("read-only", "read-write",
                                                     "read-create"):
        unreadable.append((key, name))
    if not any(col == v or col.startswith(v + ".") for v in view):
        outside.append((key, col))
check(len(polled) > 50, f"{len(polled)} OIDs read off the template")
check(not unknown, f"each is an object a module defines: {unknown}")
check(not unreadable, f"and readable, not an index: {unreadable}")
check(not outside, f"and inside the view snmpd.conf grants: {outside}")
# The names each key means to read: a column one off still resolves, to
# another object, and only the name says it is the wrong one.
MEANT = {"net.if.in": "ifHCInOctets", "net.if.out": "ifHCOutOctets",
         "net.if.in.errors": "ifInErrors", "net.if.out.errors": "ifOutErrors",
         "net.if.in.discards": "ifInDiscards", "net.if.out.discards": "ifOutDiscards",
         "net.if.status": "ifOperStatus", "net.if.adminstatus": "ifAdminStatus",
         "net.if.speed": "ifHighSpeed", "bgp.peer.state": "bgpPeerState",
         "bgp.peer.admin": "bgpPeerAdminStatus", "ospf.nbr.state": "ospfNbrState",
         "ospfv3.nbr.state": "ospfv3NbrState", "isis.adj.state": "isisISAdjState",
         "prisma.pool.util": "prPoolUtilisation", "prisma.pool.nearfull": "prPoolSubscribersNearFull",
         "prisma.pool.refusals.rate": "prPoolRefusals",
         "prisma.cgnat.sessions.fit": "prSessionsFit", "prisma.vrrp.state": "prVrrpState",
         "prisma.bundle.active": "prBundleActiveMembers", "prisma.bundle.members": "prBundleMembers",
         "prisma.dataplane.current": "prSystemDataplaneCurrent",
         "prisma.log.transport.connected": "prLogTransportConnected",
         "prisma.log.transport.dropped.rate": "prLogTransportDropped",
         "lag.port.attached": "dot3adAggPortAttachedAggID", "fib.routes": "inetCidrRouteNumber",
         "vfs.fs.error": "dskErrorFlag", "bgp4v2.peer.state": "bgp4V2PeerState"}
wrong = []
for key, col in polled:
    base = key.split("[")[0]
    if base in MEANT and (BGP4V2.get(col) or by_oid.get(col)) != MEANT[base]:
        wrong.append((key, BGP4V2.get(col) or by_oid.get(col), MEANT[base]))
check(not wrong, f"the alarmed keys read the objects they are named for: {wrong}")

# The interface table is the agent's (#498), and IF-MIB defines columns it
# does not serve.
loader = importlib.machinery.SourceFileLoader(
    "agent", os.path.join(REPO, "appliance", "prisma-routing-snmp-agent"))
spec = importlib.util.spec_from_loader("agent", loader)
agent = importlib.util.module_from_spec(spec)
loader.exec_module(agent)
card = {"name": "ge0", "mtu": 1500, "speed": 10 ** 10, "mac": "02:00:00:00:00:01",
        "admin": "up", "oper": "up", "alias": "uplink",
        **{k: 1 for k in ("in_octets", "in_ucast", "in_mcast", "in_bcast",
                          "in_discards", "in_errors", "out_octets", "out_ucast",
                          "out_mcast", "out_bcast", "out_discards", "out_errors")}}
served = {o.rsplit(".", 1)[0] for o, _, _ in agent.if_rows([card], {"ge0": 1})}
ifcols = [(k, c) for k, c in polled
          if c.startswith("1.3.6.1.2.1.2.2.1.") or c.startswith("1.3.6.1.2.1.31.1.1.1.")]
check(ifcols and all(c in served for _, c in ifcols),
      f"the {len(ifcols)} interface columns are ones the agent serves: "
      f"{[x for x in ifcols if x[1] not in served]}")

# ------------------------------------------------------------------ references
print("\n=== triggers, macros, value maps and LLD macros name what exists ===")
keys = {o["key"] for _, o, _ in walk(tpl) if "key" in o}
macros = {m["macro"] for m in tpl["macros"]}
maps = {v["name"] for v in tpl["valuemaps"]}
exprs = []
for kind, o, r in walk(tpl):
    for t in o.get("triggers", []) + o.get("trigger_prototypes", []):
        exprs.append((t["name"], t["expression"] + " " + t.get("recovery_expression", "")))
        exprs += [(t["name"], d["expression"]) for d in t.get("dependencies", [])]
for r in tpl["discovery_rules"]:
    for t in r.get("trigger_prototypes", []):
        exprs.append((t["name"], t["expression"]))
        exprs += [(t["name"], d["expression"]) for d in t.get("dependencies", [])]
check(len(exprs) > 20, f"{len(exprs)} trigger expressions")
dangling = [(n, k) for n, e in exprs
            for k in re.findall(r"/" + re.escape(tpl["template"])
                                + r"/([^,)\[]+(?:\[[^\]]*\])?)", e)
            if k not in keys]
check(not dangling, f"every item a trigger names is one the template has: {dangling}")
calc = [k for _, o, _ in walk(tpl)
        for k in re.findall(r"//([^,)\[]+(?:\[[^\]]*\])?)", o.get("params", ""))
        if k not in keys]
check(not calc, f"and every item a calculated one reads: {calc}")
used = set()
for _, o, _ in walk(tpl):
    used.update(re.findall(r"\{\$[A-Z0-9_.]+", json.dumps(o)))
for _, e in exprs:
    used.update(re.findall(r"\{\$[A-Z0-9_.]+", e))
undefined = sorted(m + "}" for m in used if m + "}" not in macros)
check(not undefined, f"every user macro used is one the template defines: {undefined}")
vm = [(o["key"], o["valuemap"]["name"]) for _, o, _ in walk(tpl)
      if "valuemap" in o and o["valuemap"]["name"] not in maps]
check(not vm, f"every value map an item names exists: {vm}")
lld = []
for r in tpl["discovery_rules"]:
    have = set(re.findall(r"\{#[A-Z0-9_.]+\}", r["snmp_oid"])) | {"{#SNMPINDEX}", "{#SNMPVALUE}"}
    for p in r["item_prototypes"] + r.get("trigger_prototypes", []):
        lld += [(r["key"], m) for m in re.findall(r"\{#[A-Z0-9_.]+\}", json.dumps(p))
                if m not in have]
    lld += [(r["key"], c["macro"]) for c in r.get("filter", {}).get("conditions", [])
            if c["macro"] not in have]
check(not lld, f"every LLD macro a prototype or filter uses, its rule discovers: {lld}")
check(shipped.count("@PEN@") == 1
      and [m["value"] for m in tpl["macros"] if m["macro"] == "{$PRISMA.PEN}"] == ["@PEN@"],
      "@PEN@ is {$PRISMA.PEN}'s default, and appears nowhere else")
check(all(r["type"] == "SNMP_AGENT" for r in tpl["discovery_rules"]),
      "every discovery rule is SNMP")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
