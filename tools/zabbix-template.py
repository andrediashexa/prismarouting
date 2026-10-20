#!/usr/bin/env python3
"""
The Zabbix 7.0 template for Prisma Routing (#514), written out as
appliance/prisma-routing-zabbix.json.in:

    python3 tools/zabbix-template.py > appliance/prisma-routing-zabbix.json.in

WHY A PROGRAM AND NOT THE FILE ALONE. The export is about a hundred kilobytes
of JSON in which every item, rule, trigger and value map carries a UUID, and a
trigger names its items by key inside an expression string. Edited by hand, a
renamed key leaves a trigger pointing at nothing and Zabbix refuses the whole
import; a duplicated UUID makes the second object replace the first. Here each
UUID is derived from what it identifies, so a regenerated file keeps them and
a re-import updates a template already in Zabbix rather than adding a second
one. test/test_zabbix_template.py holds the shipped file to this program's
output and to the product: every OID one the box serves.

JSON AND NOT YAML. Zabbix imports both the same way; the host's python3 has
json and not yaml, and the suites run there.

@PEN@ is {$PRISMA.PEN}'s default: `show snmp zabbix-template` fills in the
number the box serves PRISMA-ROUTING-MIB under, as `show snmp mib` does.
"""
import json, sys, uuid

T = "Prisma Routing by SNMP"
NS = uuid.UUID("5d1c0b8e-2f4a-4b8e-9c61-51400000514a")
P = "1.3.6.1.4.1.{$PRISMA.PEN}.1"


def uid(*k):
    # Deterministic, so a re-import updates rather than duplicates, and
    # shaped as the UUIDv4 Zabbix requires.
    return uuid.UUID(bytes=uuid.uuid5(NS, "/".join(k)).bytes, version=4).hex


def ref(key):
    return f"/{T}/{key}"


def item(name, oid, key, vt="UNSIGNED", delay="1m", units="", desc="",
         vmap=None, pre=None, tags=(), triggers=(), history="7d", trends=None,
         typ="SNMP_AGENT", params=None, proto=False):
    i = {"uuid": uid("item", key), "name": name, "type": typ}
    if oid is not None:
        i["snmp_oid"] = oid
    i["key"] = key
    if delay:
        i["delay"] = delay
    i["history"] = history
    if vt in ("CHAR", "TEXT", "LOG"):
        i["trends"] = "0"
    elif trends:
        i["trends"] = trends
    i["value_type"] = vt
    if units:
        i["units"] = units
    if params:
        i["params"] = params
    if desc:
        i["description"] = desc
    if vmap:
        i["valuemap"] = {"name": vmap}
    if pre:
        i["preprocessing"] = [dict({"type": t}, **({"parameters": p} if p else {}))
                              for t, p in pre]
    i["tags"] = [{"tag": a, "value": b} for a, b in tags]
    if triggers:
        i["trigger_prototypes" if proto else "triggers"] = list(triggers)
    return i


def trig(expr, name, prio, desc="", rec=None, deps=(), tags=(), manual=False):
    t = {"uuid": uid("trigger", name, expr), "expression": expr, "name": name,
         "priority": prio}
    if rec:
        t["recovery_mode"] = "RECOVERY_EXPRESSION"
        t["recovery_expression"] = rec
    if desc:
        t["description"] = desc
    if manual:
        t["manual_close"] = "YES"
    if deps:
        t["dependencies"] = [{"name": n, "expression": e} for n, e in deps]
    t["tags"] = [{"tag": a, "value": b} for a, b in tags]
    return t


def rule(name, oid, key, protos, delay="1h", filt=None, desc="", tprotos=()):
    r = {"uuid": uid("rule", key), "name": name, "type": "SNMP_AGENT",
         "snmp_oid": oid, "key": key, "delay": delay}
    if filt:
        r["filter"] = {"evaltype": "AND", "conditions": [
            dict({"macro": m, "value": v, "formulaid": chr(65 + n)},
                 **({"operator": op} if op else {}))
            for n, (m, v, op) in enumerate(filt)]}
    if desc:
        r["description"] = desc
    r["item_prototypes"] = protos
    if tprotos:
        r["trigger_prototypes"] = list(tprotos)
    return r


CPS = ("CHANGE_PER_SECOND", None)
X8 = ("MULTIPLIER", ["8"])
HEART = ("DISCARD_UNCHANGED_HEARTBEAT", ["1d"])

VALUEMAPS = {
    "IF-MIB::ifOperStatus": [(1, "up"), (2, "down"), (3, "testing"), (4, "unknown"),
                             (5, "dormant"), (6, "notPresent"), (7, "lowerLayerDown")],
    "IF-MIB::ifAdminStatus": [(1, "up"), (2, "down"), (3, "testing")],
    "BGP4-MIB::bgpPeerState": [(1, "idle"), (2, "connect"), (3, "active"),
                               (4, "opensent"), (5, "openconfirm"), (6, "established")],
    "BGP4-MIB::bgpPeerAdminStatus": [(1, "stop"), (2, "start")],
    "BGP4V2-MIB::bgp4V2PeerAdminStatus": [(1, "halted"), (2, "running")],
    "OSPF-MIB::ospfNbrState": [(1, "down"), (2, "attempt"), (3, "init"), (4, "twoWay"),
                               (5, "exchangeStart"), (6, "exchange"), (7, "loading"),
                               (8, "full")],
    "ISIS-MIB::isisISAdjState": [(1, "down"), (2, "initializing"), (3, "up"), (4, "failed")],
    "SNMPv2-TC::TruthValue": [(1, "true"), (2, "false")],
    "PRISMA-ROUTING-MIB::prPoolMode": [(1, "deterministic"), (2, "dynamic")],
    "PRISMA-ROUTING-MIB::prVrrpState": [(1, "init"), (2, "backup"), (3, "master"),
                                        (4, "interfaceDown")],
    "VRRPV3-MIB::vrrpv3OperationsStatus": [(1, "initialize"), (2, "backup"), (3, "master")],
    "IEEE8023-LAG-MIB::dot3adAggPortDebugMuxState": [
        (1, "detached"), (2, "waiting"), (3, "attached"), (4, "collecting"),
        (5, "distributing"), (6, "collectingDistributing")],
    "PRISMA-ROUTING-MIB::prTuningState": [(1, "tuned"), (2, "betweenPlans"),
                                          (3, "firstBoot")],
    "PRISMA-ROUTING-MIB::prSelfTestVerdict": [(1, "ready"), (2, "readyWithWarnings"),
                                              (3, "notForwarding"), (4, "failed")],
    "PRISMA-ROUTING-MIB::prAuditState": [(1, "intact"), (2, "empty"), (3, "broken"),
                                         (4, "truncated"), (5, "incomplete")],
    "PRISMA-ROUTING-MIB::prXcvrStatus": [(1, "ddm"), (2, "noDdm"), (3, "copper"),
                                         (4, "absent"), (5, "unreadable")],
    "PRISMA-ROUTING-MIB::prXcvrState": [(1, "normal"), (2, "warning"), (3, "alarm"),
                                        (4, "unknown")],
    "PRISMA-ROUTING-MIB::prXcvrLaneLevel": [(1, "normal"), (2, "highWarning"),
                                            (3, "lowWarning"), (4, "highAlarm"),
                                            (5, "lowAlarm"), (6, "unknown")],
    "PRISMA-ROUTING-MIB::prBngState": [(1, "running"), (2, "stopped"), (3, "notConfigured"),
                                       (4, "noContainers")],
    "PRISMA-ROUTING-MIB::prBngRadiusState": [(1, "active"), (2, "failed")],
    "PRISMA-ROUTING-MIB::prBngShaperDownLimiter": [(1, "shaper"), (2, "policer"),
                                                   (3, "notConfigured")],
    "ENTITY-SENSOR-MIB::EntitySensorStatus": [(1, "ok"), (2, "unavailable"),
                                              (3, "nonoperational")],
    "UCD-SNMP-MIB::UCDErrorFlag": [(0, "noError"), (1, "error")],
    "Zabbix::SNMP availability": [(0, "not available"), (1, "available"), (2, "unknown")],
}

MACROS = [
    ("{$PRISMA.PEN}", "@PEN@", "The enterprise number the box serves PRISMA-ROUTING-MIB under: `snmp enterprise-number`, or the product's."),
    ("{$SNMP.TIMEOUT}", "5m", "How long without an SNMP answer before the box is reported unreachable."),
    ("{$IFCONTROL}", "1", "1 raises 'link down' for an interface; set {$IFCONTROL:\"<ifName>\"} to 0 for one that may be down."),
    ("{$IF.UTIL.MAX}", "90", "Interface utilisation, percent of ifHighSpeed, above which a warning is raised."),
    ("{$IF.ERRORS.WARN}", "2", "Errors per second on an interface above which a warning is raised."),
    ("{$NET.IF.IFNAME.MATCHES}", ".*", "Interfaces discovered: ifName matching this."),
    ("{$NET.IF.IFNAME.NOT_MATCHES}", "^(local0|tap[0-9]+)$", "Interfaces not discovered: ifName matching this."),
    ("{$PRISMA.POOL.UTIL.WARN}", "80", "A pool's block utilisation, percent, above which a warning is raised."),
    ("{$PRISMA.POOL.UTIL.CRIT}", "95", "A pool's block utilisation, percent, above which a high is raised."),
    ("{$PRISMA.SESSIONS.FIT.WARN}", "200000", "Sessions still fitting in the CGNAT's memory below which a warning is raised."),
    ("{$PRISMA.SESSIONS.FIT.CRIT}", "20000", "Sessions still fitting in the CGNAT's memory below which a high is raised."),
    ("{$PRISMA.FIB.MIN}", "100", "FIB size below which a sudden drop is not reported (a lab or a box with a handful of routes)."),
    ("{$PRISMA.FIB.DROP.PCT}", "50", "The FIB falling below this percent of its maximum over the last hour raises an average."),
    ("{$MEMORY.UTIL.MAX}", "90", "Control-plane memory utilisation, percent, above which an average is raised."),
    ("{$PRISMA.XCVR.CONTROL}", "1", "1 raises a port's transceiver alarms; set {$PRISMA.XCVR.CONTROL:\"<port>\"} (a QSFP lane: \"<port> lane <n>\") to 0 for one whose module may be dark or absent."),
    ("{$PRISMA.XCVR.TEMP.MAX}", "70", "A module's temperature, degrees C, above which a warning is raised - beside the module's own high warning, whichever is crossed first."),
    ("{$PRISMA.XCVR.RX.MIN}", "-40", "RX power, dBm, below which a warning is raised - beside the module's own low warning. -40 is no light: raise it on a port to watch a level the module's thresholds do not."),
    ("{$PRISMA.XCVR.RX.MAX}", "10", "RX power, dBm, above which a warning is raised - beside the module's own high warning."),
    ("{$PRISMA.BFD.TRAP.HOLD}", "10m", "How long a bfdSessDown keeps its problem open after the last one arrived; the session's own state is bfdSessState."),
    ("{$PRISMA.PPPOE.MIN}", "10", "PPPoE sessions up below which a sudden drop is not reported (a lab, or a box starting to take subscribers)."),
    ("{$PRISMA.PPPOE.DROP.PCT}", "50", "PPPoE sessions up falling below this percent of their maximum over the last hour raises a high."),
    ("{$PRISMA.SHAPER.BOX.DROPS.MAX}", "0", "PPPoE shaper packets dropped per second for want of the box's shaper buffers, sustained 5m, above which a high is raised: any is a subscriber losing what its plan had room for."),
    ("{$PRISMA.SHAPER.BUFFERS.WARN}", "80", "The share of the box's shaper buffers held, percent, sustained 5m, above which a warning is raised: past 100% packets are dropped for the box, not the plan."),
    ("{$BGP4V2.INDEX.MATCHES}", "^[0-9]+\\.2\\.", "BGP4V2-MIB peers discovered, by index (instance.type.address): IPv6 only by default, since BGP4-MIB already has the IPv4 ones."),
]


def build():
    items, rules, top = [], [], []
    # ---------------------------------------------------------- the box
    items += [
        item("SNMP agent availability", None, "zabbix[host,snmp,available]",
             typ="INTERNAL", vmap="Zabbix::SNMP availability", tags=[("component", "health")],
             triggers=[trig(f"max({ref('zabbix[host,snmp,available]')},{{$SNMP.TIMEOUT}})=0",
                            "Prisma Routing: no SNMP data collection", "WARNING",
                            "The box has not answered SNMP for {$SNMP.TIMEOUT}: down, unreachable, or snmpd stopped (`snmp enabled`).",
                            tags=[("scope", "availability")])]),
        item("System name", "1.3.6.1.2.1.1.5.0", "system.name", "CHAR", "15m",
             pre=[HEART], tags=[("component", "system")]),
        item("System description", "1.3.6.1.2.1.1.1.0", "system.descr", "CHAR", "15m",
             pre=[HEART], tags=[("component", "system")]),
        item("Uptime", "1.3.6.1.2.1.25.1.1.0", "system.hw.uptime", "FLOAT", "5m", "uptime",
             pre=[("MULTIPLIER", ["0.01"])], tags=[("component", "system")],
             triggers=[trig(f"last({ref('system.hw.uptime')})<10m",
                            "Prisma Routing: the box has been restarted (uptime < 10m)",
                            "WARNING", "hrSystemUptime: the host, not the SNMP daemon.",
                            tags=[("scope", "notice")], manual=True)]),
        item("Load average (1m)", "1.3.6.1.4.1.2021.10.1.3.1", "system.cpu.load.avg1",
             "FLOAT", tags=[("component", "cpu")]),
        item("Memory: total", "1.3.6.1.4.1.2021.4.5.0", "vm.memory.total", "UNSIGNED", "15m", "B",
             pre=[("MULTIPLIER", ["1024"])], tags=[("component", "memory")]),
        item("Memory: available", "1.3.6.1.4.1.2021.4.6.0", "vm.memory.avail", "UNSIGNED", "1m", "B",
             pre=[("MULTIPLIER", ["1024"])], tags=[("component", "memory")]),
        item("Memory: buffers", "1.3.6.1.4.1.2021.4.14.0", "vm.memory.buffers", "UNSIGNED", "1m", "B",
             pre=[("MULTIPLIER", ["1024"])], tags=[("component", "memory")]),
        item("Memory: cached", "1.3.6.1.4.1.2021.4.15.0", "vm.memory.cached", "UNSIGNED", "1m", "B",
             pre=[("MULTIPLIER", ["1024"])], tags=[("component", "memory")]),
        item("Memory utilisation", None, "vm.memory.util", "FLOAT", "1m", "%", typ="CALCULATED",
             params=("100*(last(//vm.memory.total)-last(//vm.memory.avail)-last(//vm.memory.buffers)"
                     "-last(//vm.memory.cached))/last(//vm.memory.total)"),
             desc="The control plane's RAM: the dataplane's hugepages are reserved at boot and count as used.",
             tags=[("component", "memory")],
             triggers=[trig(f"min({ref('vm.memory.util')},5m)>{{$MEMORY.UTIL.MAX}}",
                            "Prisma Routing: high memory utilisation (>{$MEMORY.UTIL.MAX}% for 5m)",
                            "AVERAGE", tags=[("scope", "capacity")])]),
        item("FIB: routes (IPv4 and IPv6)", "1.3.6.1.2.1.4.24.6.0", "fib.routes", "UNSIGNED", "1m",
             desc="inetCidrRouteNumber, IPv4 and IPv6: the size of the table, not the table. ipCidrRouteNumber is left out: the box's net-snmp answers it with noSuchObject.",
             tags=[("component", "routing")],
             triggers=[trig(f"last({ref('fib.routes')})<{{$PRISMA.FIB.DROP.PCT}}/100*max({ref('fib.routes')},1h)"
                            f" and max({ref('fib.routes')},1h)>{{$PRISMA.FIB.MIN}}",
                            "Prisma Routing: the FIB fell below {$PRISMA.FIB.DROP.PCT}% of its size in the last hour",
                            "AVERAGE", "A session that dropped took its routes with it.",
                            tags=[("scope", "availability")])]),
        # ------------------------------------------------ the product
        item("Release installed", f"{P}.12.1.0", "prisma.release", "CHAR", "1h",
             pre=[HEART], tags=[("component", "system")]),
        item("Dataplane build running", f"{P}.12.2.0", "prisma.dataplane.build", "CHAR", "1h",
             pre=[HEART], tags=[("component", "system")]),
        item("Dataplane running the release installed", f"{P}.12.3.0", "prisma.dataplane.current",
             "UNSIGNED", "5m", vmap="SNMPv2-TC::TruthValue", tags=[("component", "system")],
             triggers=[trig(f"last({ref('prisma.dataplane.current')})=2",
                            "Prisma Routing: the dataplane is running the old build (reboot pending)",
                            "WARNING", "An upgrade waits for a reboot: the box forwards with the old build and refuses a commit that needs the new one.",
                            tags=[("scope", "notice")])]),
        item("Dataplane uptime", f"{P}.12.4.0", "prisma.dataplane.uptime", "FLOAT", "5m", "uptime",
             pre=[("MULTIPLIER", ["0.01"])], tags=[("component", "system")],
             desc="prSystemDataplaneUptime: VPP's own clock (#534).",
             triggers=[trig(f"last({ref('prisma.dataplane.uptime')})<10m"
                            f" and last({ref('system.hw.uptime')})>15m",
                            "Prisma Routing: the dataplane restarted without the box",
                            "AVERAGE", "VPP's clock is younger than the box's: the dataplane restarted on its own (a crash, a restart of its service, a tuning change). Every session on the box was lost with it.",
                            tags=[("scope", "availability")])]),
        # ------------------------------------------------ the box's own state (#524)
        item("Tuning: state", f"{P}.16.1.0", "prisma.tuning.state", "UNSIGNED", "5m",
             vmap="PRISMA-ROUTING-MIB::prTuningState", tags=[("component", "tuning")],
             triggers=[trig(f"last({ref('prisma.tuning.state')})<>1",
                            "Prisma Routing: the tuning waits for a reboot",
                            "WARNING", "Between two plans the dataplane runs the workers the kernel isolates now, fewer than planned; on a first boot it runs the conservative configuration. A reboot applies the plan.",
                            tags=[("scope", "notice")])]),
        item("Tuning: workers this boot", f"{P}.16.2.0", "prisma.tuning.workers", "UNSIGNED", "5m",
             tags=[("component", "tuning")]),
        item("Tuning: workers the next boot", f"{P}.16.3.0", "prisma.tuning.workers.next", "UNSIGNED", "5m",
             tags=[("component", "tuning")]),
        item("Tuning: CPUs isolated", f"{P}.16.4.0", "prisma.tuning.isolated", "CHAR", "1h",
             pre=[HEART], tags=[("component", "tuning")]),
        item("Tuning: CPUs asked to isolate", f"{P}.16.5.0", "prisma.tuning.isolated.wanted", "CHAR", "1h",
             pre=[HEART], tags=[("component", "tuning")]),
        item("Tuning: hugepages held", f"{P}.16.6.0", "prisma.tuning.hugepages", "UNSIGNED", "5m",
             tags=[("component", "tuning")],
             triggers=[trig(f"last({ref('prisma.tuning.hugepages')})<last({ref('prisma.tuning.hugepages.wanted')})",
                            "Prisma Routing: the kernel holds fewer hugepages than the tuner asked for",
                            "WARNING", "The dataplane's memory is smaller than planned: the kernel could not reserve them, or this boot came before the request.",
                            tags=[("scope", "capacity")])]),
        item("Tuning: hugepages asked for", f"{P}.16.7.0", "prisma.tuning.hugepages.wanted", "UNSIGNED", "5m",
             tags=[("component", "tuning")]),
        item("Self-test: verdict", f"{P}.17.1.0", "prisma.selftest.verdict", "UNSIGNED", "5m",
             vmap="PRISMA-ROUTING-MIB::prSelfTestVerdict", tags=[("component", "selftest")],
             triggers=[trig(f"last({ref('prisma.selftest.verdict')})=4",
                            "Prisma Routing: the boot self-test failed",
                            "HIGH", "The boot's console says which checks failed, with the reason beside each.",
                            tags=[("scope", "availability")]),
                       trig(f"last({ref('prisma.selftest.verdict')})=3",
                            "Prisma Routing: installed and healthy, with nothing to forward on",
                            "INFO", "No interface to forward on yet: the step a racked box has left.",
                            tags=[("scope", "notice")])]),
        item("Self-test: checks failed", f"{P}.17.2.0", "prisma.selftest.failures", "UNSIGNED", "5m",
             tags=[("component", "selftest")]),
        item("Self-test: warnings", f"{P}.17.3.0", "prisma.selftest.warnings", "UNSIGNED", "5m",
             tags=[("component", "selftest")]),
        item("Self-test: ran at", f"{P}.17.4.0", "prisma.selftest.ranat", "CHAR", "15m",
             pre=[HEART], tags=[("component", "selftest")]),
        item("Configuration: changes applied since boot", f"{P}.18.1.0", "prisma.config.commits",
             "UNSIGNED", "5m", tags=[("component", "configuration")]),
        item("Configuration: last change at", f"{P}.18.2.0", "prisma.config.lastat", "CHAR", "5m",
             pre=[HEART], tags=[("component", "configuration")]),
        item("Configuration: last change by", f"{P}.18.3.0", "prisma.config.lastby", "CHAR", "5m",
             pre=[HEART], tags=[("component", "configuration")],
             triggers=[trig(f"change({ref('prisma.config.lastby')})<>0",
                            "Prisma Routing: configuration changed by {ITEM.LASTVALUE}",
                            "INFO", "Closed by hand once read.", manual=True,
                            tags=[("scope", "notice")])]),
        item("Configuration: commit confirmed pending", f"{P}.18.4.0", "prisma.config.confirm.pending",
             "UNSIGNED", "1m", vmap="SNMPv2-TC::TruthValue", tags=[("component", "configuration")],
             triggers=[trig(f"last({ref('prisma.config.confirm.pending')})=1",
                            "Prisma Routing: a commit confirmed waits for its confirmation",
                            "WARNING", "The box reverts it at the deadline unless somebody confirms.",
                            tags=[("scope", "notice")])]),
        item("Configuration: commit confirmed deadline", f"{P}.18.5.0", "prisma.config.confirm.deadline",
             "CHAR", "1m", pre=[HEART], tags=[("component", "configuration")]),
        item("Configuration: reverts since boot", f"{P}.18.6.0", "prisma.config.reverts",
             "UNSIGNED", "1m", tags=[("component", "configuration")],
             triggers=[trig(f"change({ref('prisma.config.reverts')})>0",
                            "Prisma Routing: a commit confirmed was reverted",
                            "HIGH", "Nobody confirmed it: a change made and then lost, usually because it cut the session it was made from.",
                            manual=True, tags=[("scope", "availability")])]),
        item("Configuration: rollbacks since boot", f"{P}.18.7.0", "prisma.config.rollbacks",
             "UNSIGNED", "5m", tags=[("component", "configuration")]),
        item("Configuration: running differs from saved", f"{P}.18.8.0", "prisma.config.unsaved",
             "UNSIGNED", "5m", vmap="SNMPv2-TC::TruthValue", tags=[("component", "configuration")],
             triggers=[trig(f"min({ref('prisma.config.unsaved')},1h)=1",
                            "Prisma Routing: changes unsaved for an hour",
                            "WARNING", "A reboot would come back without them: 'save'.",
                            tags=[("scope", "notice")])]),
        item("Configuration trail: state", f"{P}.19.1.0", "prisma.audit.state", "UNSIGNED", "5m",
             vmap="PRISMA-ROUTING-MIB::prAuditState", tags=[("component", "audit")],
             triggers=[trig(f"last({ref('prisma.audit.state')})>=3",
                            "Prisma Routing: the configuration trail does not verify",
                            "HIGH", "Broken, truncated or incomplete: 'show cgnat history verify' names the first entry.",
                            tags=[("scope", "compliance")])]),
        item("Configuration trail: entries", f"{P}.19.2.0", "prisma.audit.entries", "UNSIGNED", "5m",
             tags=[("component", "audit")]),
        item("Configuration trail: head sequence", f"{P}.19.3.0", "prisma.audit.seq", "UNSIGNED", "5m",
             tags=[("component", "audit")]),
        item("Configuration trail: head digest", f"{P}.19.4.0", "prisma.audit.digest", "CHAR", "5m",
             history="365d", pre=[HEART], tags=[("component", "audit")],
             desc="Kept here, off the box: a trail rewritten from scratch verifies, and only a head recorded elsewhere tells it from the one that was there."),
        item("Configuration trail: entries lost", f"{P}.19.5.0", "prisma.audit.lost", "UNSIGNED", "5m",
             tags=[("component", "audit")]),
        item("CGNAT: subscribers", f"{P}.1.0", "prisma.cgnat.subscribers", tags=[("component", "cgnat")]),
        item("CGNAT: ports in use", f"{P}.2.0", "prisma.cgnat.ports", tags=[("component", "cgnat")]),
        item("CGNAT: ports in held blocks", f"{P}.3.0", "prisma.cgnat.ports.held", tags=[("component", "cgnat")]),
        item("CGNAT: subscribers near full", f"{P}.4.0", "prisma.cgnat.nearfull",
             desc="Subscribers using 90% or more of their ports.", tags=[("component", "cgnat")]),
        item("CGNAT: port refusals per second", f"{P}.5.0", "prisma.cgnat.refusals.rate", "FLOAT",
             pre=[CPS], units="rps", tags=[("component", "cgnat")]),
        item("CGNAT: packets dropped per second", f"{P}.6.0", "prisma.cgnat.drops.rate", "FLOAT",
             pre=[CPS], units="pps", tags=[("component", "cgnat")]),
        item("CGNAT: sessions", f"{P}.10.3.0", "prisma.cgnat.sessions", tags=[("component", "cgnat")]),
        item("CGNAT: sessions that still fit in memory", f"{P}.10.4.0", "prisma.cgnat.sessions.fit",
             desc="At zero, new sessions are refused while the open ones go on.",
             tags=[("component", "cgnat")],
             triggers=[
                 trig(f"last({ref('prisma.cgnat.sessions.fit')})<{{$PRISMA.SESSIONS.FIT.CRIT}}",
                      "Prisma Routing: CGNAT memory nearly exhausted (fewer than {$PRISMA.SESSIONS.FIT.CRIT} sessions fit)",
                      "HIGH", tags=[("scope", "capacity")]),
                 trig(f"last({ref('prisma.cgnat.sessions.fit')})<{{$PRISMA.SESSIONS.FIT.WARN}}",
                      "Prisma Routing: CGNAT memory running low (fewer than {$PRISMA.SESSIONS.FIT.WARN} sessions fit)",
                      "WARNING", tags=[("scope", "capacity")],
                      deps=[("Prisma Routing: CGNAT memory nearly exhausted (fewer than {$PRISMA.SESSIONS.FIT.CRIT} sessions fit)",
                             f"last({ref('prisma.cgnat.sessions.fit')})<{{$PRISMA.SESSIONS.FIT.CRIT}}")])]),
        item("Connection records: IPFIX over UDP enabled", f"{P}.11.1.0", "prisma.log.ipfix.enabled",
             "UNSIGNED", "5m", vmap="SNMPv2-TC::TruthValue", tags=[("component", "logging")]),
        item("Connection records: IPFIX over UDP records per second", f"{P}.11.2.0",
             "prisma.log.ipfix.records.rate", "FLOAT", pre=[CPS], units="rps",
             tags=[("component", "logging")]),
        item("Connection records: IPFIX over UDP dropped per second", f"{P}.11.3.0",
             "prisma.log.ipfix.dropped.rate", "FLOAT", pre=[CPS], units="rps",
             tags=[("component", "logging")],
             triggers=[trig(f"last({ref('prisma.log.ipfix.dropped.rate')})>0",
                            "Prisma Routing: IPFIX records are being lost",
                            "HIGH", "A gap in the compliance log.", tags=[("scope", "compliance")])]),
        item("Identity: subscribers named", f"{P}.15.1.0", "prisma.identity.entries",
             tags=[("component", "identity")]),
        item("Identity: table read at", f"{P}.15.2.0", "prisma.identity.readat", "CHAR", "15m",
             pre=[HEART], tags=[("component", "identity")]),
        item("RADIUS accounting received per second", f"{P}.15.3.0", "prisma.radius.received.rate",
             "FLOAT", pre=[CPS], units="rps", tags=[("component", "identity")]),
        item("RADIUS accounting applied per second", f"{P}.15.4.0", "prisma.radius.applied.rate",
             "FLOAT", pre=[CPS], units="rps", tags=[("component", "identity")]),
        item("RADIUS accounting refused per second", f"{P}.15.5.0", "prisma.radius.refused.rate",
             "FLOAT", pre=[CPS], units="rps", tags=[("component", "identity")],
             triggers=[trig(f"last({ref('prisma.radius.refused.rate')})>0",
                            "Prisma Routing: RADIUS accounting refused",
                            "WARNING", "A bad authenticator or a malformed packet; the first is usually a shared secret that differs between the BNG and the box.",
                            tags=[("scope", "compliance")])]),
        # ------------------------------------------------ PPPoE (#597)
        item("PPPoE: control plane", f"{P}.22.1.0", "prisma.pppoe.state", "UNSIGNED", "1m",
             vmap="PRISMA-ROUTING-MIB::prBngState", tags=[("component", "pppoe")],
             desc="prBngState: accel-pppd in the prisma-bng container. notConfigured on a box with no `pppoe`.",
             triggers=[trig(f"last({ref('prisma.pppoe.state')})=2",
                            "Prisma Routing: PPPoE is configured and its control plane is not running",
                            "HIGH", "No subscriber can dial. `show pppoe summary` on the box; the container restarts by itself after a crash, so one that stays down did not start.",
                            tags=[("scope", "availability")])]),
        item("PPPoE: control plane uptime", f"{P}.22.2.0", "prisma.pppoe.uptime", "FLOAT", "5m", "uptime",
             pre=[("MULTIPLIER", ["0.01"])], tags=[("component", "pppoe")],
             desc="prBngUptime: the PPPoE counters count from then. Not served while the control plane does not run.",
             triggers=[trig(f"last({ref('prisma.pppoe.uptime')})<10m"
                            f" and last({ref('system.hw.uptime')})>15m",
                            "Prisma Routing: the PPPoE control plane restarted without the box",
                            "AVERAGE", "A commit that changed `pppoe`, or a crash: every PPPoE session dropped and redialled.",
                            tags=[("scope", "availability")])]),
        item("PPPoE: sessions up", f"{P}.22.3.0", "prisma.pppoe.sessions", tags=[("component", "pppoe")],
             triggers=[trig(f"last({ref('prisma.pppoe.sessions')})<{{$PRISMA.PPPOE.DROP.PCT}}/100*max({ref('prisma.pppoe.sessions')},1h)"
                            f" and max({ref('prisma.pppoe.sessions')},1h)>{{$PRISMA.PPPOE.MIN}}",
                            "Prisma Routing: PPPoE sessions fell below {$PRISMA.PPPOE.DROP.PCT}% of the last hour's",
                            "HIGH", "Subscribers dropped together: an access link, the control plane, or the RADIUS servers refusing the redials.",
                            tags=[("scope", "availability")])]),
        item("PPPoE: sessions being set up", f"{P}.22.4.0", "prisma.pppoe.starting", tags=[("component", "pppoe")],
             desc="High for long is subscribers stuck at authentication."),
        item("PPPoE: sessions started per second", f"{P}.22.5.0", "prisma.pppoe.started.rate", "FLOAT",
             pre=[CPS], units="sps", tags=[("component", "pppoe")]),
        item("PPPoE: sessions stopped per second", f"{P}.22.6.0", "prisma.pppoe.stopped.rate", "FLOAT",
             pre=[CPS], units="sps", tags=[("component", "pppoe")]),
        item("PPPoE: Disconnect-ACKs per second", f"{P}.22.7.0", "prisma.pppoe.disconnect.ack.rate", "FLOAT",
             pre=[CPS], units="rps", tags=[("component", "pppoe")]),
        item("PPPoE: Disconnect-NAKs per second", f"{P}.22.8.0", "prisma.pppoe.disconnect.nak.rate", "FLOAT",
             pre=[CPS], units="rps", tags=[("component", "pppoe")]),
        item("PPPoE: CoA-ACKs per second", f"{P}.22.9.0", "prisma.pppoe.coa.ack.rate", "FLOAT",
             pre=[CPS], units="rps", tags=[("component", "pppoe")]),
        item("PPPoE: CoA-NAKs per second", f"{P}.22.10.0", "prisma.pppoe.coa.nak.rate", "FLOAT",
             pre=[CPS], units="rps", tags=[("component", "pppoe")]),
        item("PPPoE: Disconnect and CoA requests unanswered per second", f"{P}.22.11.0",
             "prisma.pppoe.dae.dropped.rate", "FLOAT", pre=[CPS], units="rps", tags=[("component", "pppoe")],
             triggers=[trig(f"last({ref('prisma.pppoe.dae.dropped.rate')})>0",
                            "Prisma Routing: Disconnect or CoA requests are answered with nothing",
                            "WARNING", "A source that is not a `dynamic-authorization client`, or a secret that differs between it and the box: the RADIUS side waits for an answer that does not come.",
                            tags=[("scope", "availability")])]),
        # ------------------------------------------------ PPPoE shapers (#610)
        item("PPPoE shaper: download enforced by", f"{P}.22.30.1.0", "prisma.pppoe.shaper.down.limiter",
             "UNSIGNED", "15m", vmap="PRISMA-ROUTING-MIB::prBngShaperDownLimiter",
             tags=[("component", "pppoe")],
             desc="prBngShaperDownLimiter: the dataplane's shaper (past the plan waits) or VPP's policer (past the plan is dropped), `rate-limit download`."),
        item("PPPoE shaper: upload enforced by", f"{P}.22.30.2.0", "prisma.pppoe.shaper.up.limiter",
             "UNSIGNED", "15m", vmap="PRISMA-ROUTING-MIB::prBngShaperDownLimiter",
             tags=[("component", "pppoe")], desc="prBngShaperUpLimiter, `rate-limit upload`."),
        item("PPPoE shaper: sessions shaped, download", f"{P}.22.30.3.0", "prisma.pppoe.shaper.down.sessions",
             tags=[("component", "pppoe")]),
        item("PPPoE shaper: sessions shaped, upload", f"{P}.22.30.4.0", "prisma.pppoe.shaper.up.sessions",
             tags=[("component", "pppoe")]),
        item("PPPoE shaper: queues now", f"{P}.22.30.5.0", "prisma.pppoe.shaper.queues",
             tags=[("component", "pppoe")],
             desc="prBngShaperQueues: shapers holding a queue - subscribers offering more than their plan at this moment."),
        item("PPPoE shaper: packets queued", f"{P}.22.30.6.0", "prisma.pppoe.shaper.queued.packets",
             tags=[("component", "pppoe")]),
        item("PPPoE shaper: bytes queued", f"{P}.22.30.7.0", "prisma.pppoe.shaper.queued.bytes",
             units="B", tags=[("component", "pppoe")]),
        item("PPPoE shaper: packets passed per second", f"{P}.22.30.8.0", "prisma.pppoe.shaper.passed.rate",
             "FLOAT", pre=[CPS], units="pps", tags=[("component", "pppoe")],
             desc="prBngShaperPassed: within the plan, sent at once. Every shaper counter is the dataplane's running total, kept through a session's end; it restarts with the dataplane."),
        item("PPPoE shaper: packets delayed per second", f"{P}.22.30.9.0", "prisma.pppoe.shaper.delayed.rate",
             "FLOAT", pre=[CPS], units="pps", tags=[("component", "pppoe")],
             desc="prBngShaperDelayed: past the plan, waited in the session's queue and sent."),
        item("PPPoE shaper: dropped, session queue full, per second", f"{P}.22.30.10.0",
             "prisma.pppoe.shaper.drop.full.rate", "FLOAT", pre=[CPS], units="pps",
             tags=[("component", "pppoe")],
             desc="prBngShaperDroppedQueueFull: subscribers offering more than their plan for longer than their queue holds - what a plan is for."),
        item("PPPoE shaper: dropped by CoDel per second", f"{P}.22.30.11.0",
             "prisma.pppoe.shaper.drop.codel.rate", "FLOAT", pre=[CPS], units="pps",
             tags=[("component", "pppoe")],
             desc="prBngShaperDroppedCodel: TCP flows told to slow down to their plan."),
        item("PPPoE shaper: dropped for the box's buffers per second", f"{P}.22.30.12.0",
             "prisma.pppoe.shaper.drop.box.rate", "FLOAT", pre=[CPS], units="pps",
             tags=[("component", "pppoe")],
             desc="prBngShaperDroppedBoxBuffers: the box's shapers held every buffer they may, and a packet its plan had room for was dropped.",
             triggers=[trig(f"min({ref('prisma.pppoe.shaper.drop.box.rate')},5m)>{{$PRISMA.SHAPER.BOX.DROPS.MAX}}",
                            "Prisma Routing: PPPoE shapers dropping for want of the box's buffers (>{$PRISMA.SHAPER.BOX.DROPS.MAX} pps for 5m)",
                            "HIGH", "Subscribers are losing packets their plans had room for: the shapers' queues together hold the box's share of buffers. `show pppoe summary`; more buffers (the dataplane's pool) or fewer sessions shaped at once.",
                            tags=[("scope", "capacity")])]),
        item("PPPoE shaper: bits sent", f"{P}.22.30.13.0", "prisma.pppoe.shaper.bits",
             "FLOAT", pre=[CPS, X8], units="bps", tags=[("component", "pppoe")],
             desc="prBngShaperOctets: what the shapers sent, passed and delayed together."),
        item("PPPoE shaper: buffers held", f"{P}.22.30.14.0", "prisma.pppoe.shaper.buffers",
             tags=[("component", "pppoe")]),
        item("PPPoE shaper: buffers the shapers may hold", f"{P}.22.30.15.0", "prisma.pppoe.shaper.buffers.cap",
             "UNSIGNED", "15m", tags=[("component", "pppoe")],
             desc="prBngShaperBuffersCap: a quarter of the dataplane's pool, split over the threads that own sessions."),
        item("PPPoE shaper: buffer utilisation", None, "prisma.pppoe.shaper.buffers.util", "FLOAT", "1m", "%",
             typ="CALCULATED",
             params="100*last(//prisma.pppoe.shaper.buffers)/last(//prisma.pppoe.shaper.buffers.cap)",
             desc="The box's shaper buffers held, of the most they may hold. A thread at its own share drops while another has room, so drops can start below 100%.",
             tags=[("component", "pppoe")],
             triggers=[trig(f"min({ref('prisma.pppoe.shaper.buffers.util')},5m)>{{$PRISMA.SHAPER.BUFFERS.WARN}}",
                            "Prisma Routing: PPPoE shapers hold over {$PRISMA.SHAPER.BUFFERS.WARN}% of their buffers (5m)",
                            "WARNING", "Near the share past which shaped packets are dropped for the box, not the plan.",
                            tags=[("scope", "capacity")])]),
    ]
    # ---------------------------------------------------------- traps
    traps = [
        ("linkDown", "linkDown|1\\.3\\.6\\.1\\.6\\.3\\.1\\.1\\.5\\.3\\b"),
        ("linkUp", "linkUp|1\\.3\\.6\\.1\\.6\\.3\\.1\\.1\\.5\\.4\\b"),
        ("bgpBackwardTransition", "bgpBackwardTrans|1\\.3\\.6\\.1\\.2\\.1\\.15\\.0\\.2\\b"),
        ("bgpEstablished", "bgpEstablished|1\\.3\\.6\\.1\\.2\\.1\\.15\\.0\\.1\\b"),
        ("ospfNbrStateChange", "ospfNbrStateChange|1\\.3\\.6\\.1\\.2\\.1\\.14\\.16\\.2\\.2\\b"),
        ("vrrpStateChange", "vrrpStateChange"),
        ("poolNearlyFull", "poolNearlyFull"),
        ("portsRefused", "portsRefused"),
        ("recordsTransportChange", "recordsTransportChange"),
        ("recordsLost", "recordsLost"),
        ("bundleMembersChange", "bundleMembersChange"),
        ("dataplaneNotCurrent", "dataplaneNotCurrent"),
        ("selfTestFailed", "selfTestFailed"),
        ("configReverted", "configReverted"),
        ("tuningBetweenPlans", "tuningBetweenPlans"),
        ("auditTrailDamaged", "auditTrailDamaged"),
        # #588 phase 3: the box's record, where an object holds it.
        ("poolExhausted", "poolExhausted"),
        ("transceiverStateChange", "transceiverStateChange"),
        # #559: BFD-STD-MIB's own (RFC 7331), from the host's watcher of
        # events - bfdSessUp is { bfdNotifications 1 }, bfdSessDown 2.
        ("bfdSessDown", "bfdSessDown|1\\.3\\.6\\.1\\.2\\.1\\.222\\.0\\.2\\b"),
        ("bfdSessUp", "bfdSessUp|1\\.3\\.6\\.1\\.2\\.1\\.222\\.0\\.1\\b"),
        # #496 phase 5: ENTITY-MIB's own, a module inserted, removed or
        # swapped - entConfigChange is { entityMIBTrapPrefix 1 }.
        ("entConfigChange", "entConfigChange|1\\.3\\.6\\.1\\.2\\.1\\.47\\.2\\.0\\.1\\b"),
    ]
    # A bfdSessDown raises a problem, cleared {$PRISMA.BFD.TRAP.HOLD} after
    # the last one or by hand (#559). Not paired with bfdSessUp: Zabbix
    # cannot tell which session a LOG item's value is about, and one
    # session's up would clear another's down.
    bfd_down = 'snmptrap["' + dict(traps)["bfdSessDown"] + '"]'
    on_trap = {"bfdSessDown": [trig(
        f"nodata({ref(bfd_down)},{{$PRISMA.BFD.TRAP.HOLD}})=0",
        "Prisma Routing: a BFD session went down (bfdSessDown)", "HIGH",
        "A neighbour stopped answering BFD, or said its session was down. The notification "
        "carries bfdSessDiag.<bfdSessIndex>: the session's row in bfdSessTable and its "
        "diagnostic (1 is the detection time expiring, 3 the neighbour saying down). "
        "Whether it came back is bfdSessUp's latest value, and `show bfd peers` on the box.",
        tags=[("scope", "availability")], manual=True)]}
    for name, rx in traps:
        items.append(item(f"Notification: {name}", None, f'snmptrap["{rx}"]', "LOG", None,
                          typ="SNMP_TRAP", history="31d", tags=[("component", "notification")],
                          desc="Received through Zabbix's SNMP trap receiver; the box sends it to every `snmp trap-host`.",
                          triggers=on_trap.get(name, ())))
    items.append(item("Notification: anything else", None, "snmptrap.fallback", "LOG", None,
                      typ="SNMP_TRAP", history="31d", tags=[("component", "notification")]))

    # ------------------------------------------------ discovery: CPUs, disks
    rules.append(rule("CPU discovery", "discovery[{#SNMPVALUE},1.3.6.1.2.1.25.3.3.1.2]",
                      "system.cpu.discovery", [
                          item("CPU {#SNMPINDEX}: utilisation", "1.3.6.1.2.1.25.3.3.1.2.{#SNMPINDEX}",
                               "system.cpu.util[hrProcessorLoad.{#SNMPINDEX}]", "FLOAT", units="%",
                               tags=[("component", "cpu")])],
                      desc="hrProcessorLoad per core. No trigger: the dataplane's worker cores poll at 100% by design."))
    rules.append(rule("Disk discovery", "discovery[{#DSKPATH},1.3.6.1.4.1.2021.9.1.2]",
                      "vfs.fs.discovery", [
                          item("{#DSKPATH}: space used", "1.3.6.1.4.1.2021.9.1.9.{#SNMPINDEX}",
                               "vfs.fs.pused[dskPercent.{#SNMPINDEX}]", "FLOAT", "5m", "%",
                               tags=[("component", "storage"), ("filesystem", "{#DSKPATH}")]),
                          item("{#DSKPATH}: below its minimum free", "1.3.6.1.4.1.2021.9.1.100.{#SNMPINDEX}",
                               "vfs.fs.error[dskErrorFlag.{#SNMPINDEX}]", "UNSIGNED", "5m",
                               vmap="UCD-SNMP-MIB::UCDErrorFlag",
                               tags=[("component", "storage"), ("filesystem", "{#DSKPATH}")],
                               triggers=[trig(f"last({ref('vfs.fs.error[dskErrorFlag.{#SNMPINDEX}]')})=1",
                                              "Prisma Routing: {#DSKPATH} is below its minimum free space",
                                              "AVERAGE", "On /var/log/prisma-routing this is the retention log's filesystem filling.",
                                              tags=[("scope", "capacity")])], proto=True)]))

    # ------------------------------------------------ interfaces
    k = lambda s: f"net.if.{s}[{{#SNMPINDEX}}]"
    ifp = [
        item("Interface {#IFNAME}({#IFALIAS}): bits received", "1.3.6.1.2.1.31.1.1.1.6.{#SNMPINDEX}",
             k("in"), "FLOAT", units="bps", pre=[CPS, X8], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): bits sent", "1.3.6.1.2.1.31.1.1.1.10.{#SNMPINDEX}",
             k("out"), "FLOAT", units="bps", pre=[CPS, X8], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): inbound errors", "1.3.6.1.2.1.2.2.1.14.{#SNMPINDEX}",
             k("in.errors"), "FLOAT", pre=[CPS], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): outbound errors", "1.3.6.1.2.1.2.2.1.20.{#SNMPINDEX}",
             k("out.errors"), "FLOAT", pre=[CPS], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): inbound discards", "1.3.6.1.2.1.2.2.1.13.{#SNMPINDEX}",
             k("in.discards"), "FLOAT", pre=[CPS], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): outbound discards", "1.3.6.1.2.1.2.2.1.19.{#SNMPINDEX}",
             k("out.discards"), "FLOAT", pre=[CPS], tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): operational status", "1.3.6.1.2.1.2.2.1.8.{#SNMPINDEX}",
             k("status"), vmap="IF-MIB::ifOperStatus", tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): administrative status", "1.3.6.1.2.1.2.2.1.7.{#SNMPINDEX}",
             k("adminstatus"), vmap="IF-MIB::ifAdminStatus",
             desc="Polled as often as the operational status: a slower poll raised 'link down' for an interface an operator had just shut, with the old 'up' still stored.",
             tags=[("component", "network"), ("interface", "{#IFNAME}")]),
        item("Interface {#IFNAME}({#IFALIAS}): speed", "1.3.6.1.2.1.31.1.1.1.15.{#SNMPINDEX}",
             k("speed"), "UNSIGNED", "5m", "bps", pre=[("MULTIPLIER", ["1000000"])],
             tags=[("component", "network"), ("interface", "{#IFNAME}")]),
    ]
    iftp = [
        trig(f"{{$IFCONTROL:\"{{#IFNAME}}\"}}=1 and last({ref(k('adminstatus'))})=1 and last({ref(k('status'))})=2",
             "Prisma Routing: interface {#IFNAME}({#IFALIAS}) is down", "AVERAGE",
             "Administratively up and operationally down. {$IFCONTROL:\"<ifName>\"}=0 silences one.",
             tags=[("scope", "availability")], manual=True),
        trig(f"(avg({ref(k('in'))},15m)>({{$IF.UTIL.MAX:\"{{#IFNAME}}\"}}/100)*last({ref(k('speed'))})"
             f" or avg({ref(k('out'))},15m)>({{$IF.UTIL.MAX:\"{{#IFNAME}}\"}}/100)*last({ref(k('speed'))}))"
             f" and last({ref(k('speed'))})>0",
             "Prisma Routing: interface {#IFNAME}({#IFALIAS}) above {$IF.UTIL.MAX:\"{#IFNAME}\"}% of its speed",
             "WARNING", tags=[("scope", "performance")], manual=True),
        trig(f"min({ref(k('in.errors'))},5m)>{{$IF.ERRORS.WARN:\"{{#IFNAME}}\"}}"
             f" or min({ref(k('out.errors'))},5m)>{{$IF.ERRORS.WARN:\"{{#IFNAME}}\"}}",
             "Prisma Routing: interface {#IFNAME}({#IFALIAS}) has errors",
             "WARNING", tags=[("scope", "performance")], manual=True),
    ]
    rules.append(rule("Network interfaces discovery",
                      "discovery[{#IFNAME},1.3.6.1.2.1.31.1.1.1.1,{#IFALIAS},1.3.6.1.2.1.31.1.1.1.18]",
                      "net.if.discovery", ifp,
                      filt=[("{#IFNAME}", "{$NET.IF.IFNAME.MATCHES}", None),
                            ("{#IFNAME}", "{$NET.IF.IFNAME.NOT_MATCHES}", "NOT_MATCHES_REGEX")],
                      desc="The dataplane's cards, under the ifIndex the agent keeps for each for good (#498).",
                      tprotos=iftp))

    # ------------------------------------------------ CGNAT pools and workers
    pk = lambda s: f"prisma.pool.{s}[{{#SNMPINDEX}}]"
    pool = lambda name, col, key, **kw: item(f"Pool {{#POOLNAME}}: {name}", f"{P}.10.1.1.{col}.{{#SNMPINDEX}}",
                                             pk(key), tags=[("component", "cgnat"), ("pool", "{#POOLNAME}")], **kw)
    crit = f"last({ref(pk('util'))})>={{$PRISMA.POOL.UTIL.CRIT}}"
    rules.append(rule("CGNAT pool discovery", f"discovery[{{#POOLNAME}},{P}.10.1.1.2]",
                      "prisma.pool.discovery", [
                          pool("mode", 3, "mode", delay="1h", vmap="PRISMA-ROUTING-MIB::prPoolMode"),
                          pool("capacity (subscribers at once)", 8, "capacity", delay="1h"),
                          pool("guaranteed (subscribers on any worker)", 9, "guaranteed", delay="1h"),
                          pool("subscribers", 10, "subscribers"),
                          pool("block utilisation", 12, "util", units="%", triggers=[
                              trig(crit, "Prisma Routing: pool {#POOLNAME} at {$PRISMA.POOL.UTIL.CRIT}% of its blocks",
                                   "HIGH", tags=[("scope", "capacity")]),
                              trig(f"last({ref(pk('util'))})>={{$PRISMA.POOL.UTIL.WARN}}",
                                   "Prisma Routing: pool {#POOLNAME} at {$PRISMA.POOL.UTIL.WARN}% of its blocks",
                                   "WARNING", tags=[("scope", "capacity")],
                                   deps=[("Prisma Routing: pool {#POOLNAME} at {$PRISMA.POOL.UTIL.CRIT}% of its blocks", crit)])],
                               proto=True),
                          pool("sessions", 13, "sessions"),
                          pool("ports in use", 14, "ports"),
                          pool("subscribers near full", 15, "nearfull", triggers=[
                              trig(f"last({ref(pk('nearfull'))})>0",
                                   "Prisma Routing: pool {#POOLNAME} has subscribers at 90% of their ports",
                                   "WARNING", "The early warning: at 100% a subscriber is refused ports.",
                                   tags=[("scope", "capacity")])], proto=True),
                          pool("port refusals per second", 16, "refusals.rate", vt="FLOAT", units="rps",
                               pre=[CPS], triggers=[
                                   trig(f"last({ref(pk('refusals.rate'))})>0",
                                        "Prisma Routing: pool {#POOLNAME} is refusing ports",
                                        "WARNING", "A subscriber could not open a connection.",
                                        tags=[("scope", "capacity")])], proto=True),
                      ]))
    wk = lambda s: f"prisma.worker.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("CGNAT worker discovery", f"discovery[{{#WORKERSESSIONS}},{P}.10.5.1.3]",
                      "prisma.worker.discovery", [
                          item("Worker {#SNMPINDEX}: subscribers", f"{P}.10.5.1.2.{{#SNMPINDEX}}", wk("subscribers"),
                               tags=[("component", "cgnat"), ("worker", "{#SNMPINDEX}")]),
                          item("Worker {#SNMPINDEX}: sessions", f"{P}.10.5.1.3.{{#SNMPINDEX}}", wk("sessions"),
                               tags=[("component", "cgnat"), ("worker", "{#SNMPINDEX}")]),
                          item("Worker {#SNMPINDEX}: ports in use", f"{P}.10.5.1.4.{{#SNMPINDEX}}", wk("ports"),
                               tags=[("component", "cgnat"), ("worker", "{#SNMPINDEX}")]),
                      ], desc="One row per dataplane thread (index = thread + 1). A worker far ahead of the others is the early sign of refusals with blocks free elsewhere."))
    tk = lambda s: f"prisma.log.transport.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("Connection records transport discovery", f"discovery[{{#TRANSPORT}},{P}.11.10.1.2]",
                      "prisma.log.transport.discovery", [
                          item("Records transport {#TRANSPORT}: enabled", f"{P}.11.10.1.3.{{#SNMPINDEX}}", tk("enabled"),
                               delay="5m", vmap="SNMPv2-TC::TruthValue", tags=[("component", "logging")]),
                          item("Records transport {#TRANSPORT}: connected", f"{P}.11.10.1.4.{{#SNMPINDEX}}", tk("connected"),
                               vmap="SNMPv2-TC::TruthValue", tags=[("component", "logging")]),
                          item("Records transport {#TRANSPORT}: records per second", f"{P}.11.10.1.5.{{#SNMPINDEX}}",
                               tk("records.rate"), "FLOAT", units="rps", pre=[CPS], tags=[("component", "logging")]),
                          item("Records transport {#TRANSPORT}: spilled per second", f"{P}.11.10.1.7.{{#SNMPINDEX}}",
                               tk("spilled.rate"), "FLOAT", units="rps", pre=[CPS], tags=[("component", "logging")]),
                          item("Records transport {#TRANSPORT}: dropped per second", f"{P}.11.10.1.8.{{#SNMPINDEX}}",
                               tk("dropped.rate"), "FLOAT", units="rps", pre=[CPS], tags=[("component", "logging")],
                               triggers=[trig(f"last({ref(tk('dropped.rate'))})>0",
                                              "Prisma Routing: {#TRANSPORT} connection records are being lost",
                                              "HIGH", "A gap in the compliance log.",
                                              tags=[("scope", "compliance")])], proto=True),
                      ], tprotos=[trig(f"last({ref(tk('enabled'))})=1 and min({ref(tk('connected'))},5m)=2",
                                       "Prisma Routing: {#TRANSPORT} records transport disconnected for 5m",
                                       "AVERAGE", "The records go to the spill file until the collector is back.",
                                       tags=[("scope", "compliance")])]))
    vk = lambda s: f"prisma.vrrp.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("VRRP router discovery", f"discovery[{{#VRRPIF}},{P}.13.1.1.2,{{#VRID}},{P}.13.1.1.3]",
                      "prisma.vrrp.discovery", [
                          item("VRRP {#VRRPIF} VRID {#VRID}: priority advertised", f"{P}.13.1.1.4.{{#SNMPINDEX}}",
                               vk("priority"), tags=[("component", "ha")]),
                          item("VRRP {#VRRPIF} VRID {#VRID}: state", f"{P}.13.1.1.5.{{#SNMPINDEX}}", vk("state"),
                               vmap="PRISMA-ROUTING-MIB::prVrrpState", tags=[("component", "ha")], triggers=[
                                   trig(f"change({ref(vk('state'))})<>0",
                                        "Prisma Routing: VRRP {#VRRPIF} VRID {#VRID} changed state to {ITEM.VALUE}",
                                        "WARNING", "A takeover.", tags=[("scope", "availability")], manual=True),
                                   trig(f"last({ref(vk('state'))})=4",
                                        "Prisma Routing: VRRP {#VRRPIF} VRID {#VRID}: its interface is down",
                                        "AVERAGE", tags=[("scope", "availability")])], proto=True),
                      ]))
    bk = lambda s: f"prisma.bundle.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("LACP bundle discovery", f"discovery[{{#BUNDLE}},{P}.14.1.1.2]",
                      "prisma.bundle.discovery", [
                          item("Bundle {#BUNDLE}: members configured", f"{P}.14.1.1.4.{{#SNMPINDEX}}", bk("members"),
                               tags=[("component", "lacp")]),
                          item("Bundle {#BUNDLE}: members carrying traffic", f"{P}.14.1.1.5.{{#SNMPINDEX}}", bk("active"),
                               tags=[("component", "lacp")]),
                      ], tprotos=[
                          trig(f"last({ref(bk('active'))})=0 and last({ref(bk('members'))})>0",
                               "Prisma Routing: bundle {#BUNDLE} has no member carrying traffic",
                               "HIGH", tags=[("scope", "availability")]),
                          trig(f"last({ref(bk('active'))})<last({ref(bk('members'))})",
                               "Prisma Routing: bundle {#BUNDLE} running on fewer members than it has",
                               "AVERAGE", "Up in IF-MIB and at a fraction of its capacity.",
                               tags=[("scope", "availability")],
                               deps=[("Prisma Routing: bundle {#BUNDLE} has no member carrying traffic",
                                      f"last({ref(bk('active'))})=0 and last({ref(bk('members'))})>0")])]))

    # ------------------------------------------------ PPPoE's RADIUS servers (#597)
    rk = lambda s: f"prisma.pppoe.radius.{s}[{{#SNMPINDEX}}]"
    rad = lambda name, col, key, **kw: item(f"RADIUS {{#RADIUSSERVER}}: {name}", f"{P}.22.20.1.{col}.{{#SNMPINDEX}}",
                                            rk(key), tags=[("component", "pppoe"), ("radius", "{#RADIUSSERVER}")], **kw)
    rules.append(rule("PPPoE RADIUS server discovery", f"discovery[{{#RADIUSSERVER}},{P}.22.20.1.2]",
                      "prisma.pppoe.radius.discovery", [
                          rad("state", 3, "state", vmap="PRISMA-ROUTING-MIB::prBngRadiusState", triggers=[
                              trig(f"last({ref(rk('state'))})=2",
                                   "Prisma Routing: RADIUS server {#RADIUSSERVER} is marked down",
                                   "AVERAGE", "It left `retries` sends in a row unanswered and is skipped for its dead-time; requests go to the next server. With no server left, no subscriber logs in.",
                                   tags=[("scope", "availability")])], proto=True),
                          rad("Access-Requests per second", 4, "auth.sent.rate", vt="FLOAT", units="rps", pre=[CPS]),
                          rad("Access-Accepts per second", 5, "auth.accept.rate", vt="FLOAT", units="rps", pre=[CPS]),
                          rad("Access-Rejects per second", 6, "auth.reject.rate", vt="FLOAT", units="rps", pre=[CPS],
                              desc="A wrong password, an unknown or a suspended subscriber: collected, not alarmed."),
                          rad("Access-Requests unanswered per second", 7, "auth.timeout.rate", vt="FLOAT", units="rps",
                              pre=[CPS], triggers=[
                                  trig(f"min({ref(rk('auth.timeout.rate'))},5m)>0",
                                       "Prisma Routing: RADIUS server {#RADIUSSERVER} is not answering Access-Requests",
                                       "WARNING", "Unanswered sends for five minutes: the server, the path to it, or a secret it does not share with the box.",
                                       tags=[("scope", "availability")])], proto=True),
                          rad("Accounting-Requests per second", 8, "acct.sent.rate", vt="FLOAT", units="rps", pre=[CPS]),
                          rad("Accounting-Requests unanswered per second", 9, "acct.timeout.rate", vt="FLOAT",
                              units="rps", pre=[CPS]),
                      ], delay="5m", desc="PRISMA-ROUTING-MIB's prBngRadiusTable (#597): a row per `aaa radius` server while PPPoE's control plane runs, indexed by its address. Every counter starts again at a restart of the control plane."))

    # ------------------------------------------------ transceivers (#590)
    xk = lambda s: f"prisma.xcvr.{s}[{{#SNMPINDEX}}]"
    xc = '{$PRISMA.XCVR.CONTROL:"{#XCVRNAME}"}=1'
    rules.append(rule("Transceiver discovery", f"discovery[{{#XCVRNAME}},{P}.20.1.1.2]",
                      "prisma.xcvr.discovery", [
                          item("Transceiver {#XCVRNAME}: status", f"{P}.20.1.1.3.{{#SNMPINDEX}}", xk("status"),
                               delay="5m", vmap="PRISMA-ROUTING-MIB::prXcvrStatus",
                               tags=[("component", "optics"), ("interface", "{#XCVRNAME}")]),
                          item("Transceiver {#XCVRNAME}: serial number", f"{P}.20.1.1.6.{{#SNMPINDEX}}", xk("serial"),
                               "CHAR", "1h", pre=[HEART], desc="A new value is a module swapped in the cage.",
                               tags=[("component", "optics"), ("interface", "{#XCVRNAME}")]),
                          item("Transceiver {#XCVRNAME}: temperature", f"{P}.20.1.1.8.{{#SNMPINDEX}}", xk("temp"),
                               "FLOAT", units="°C", pre=[("MULTIPLIER", ["0.01"])],
                               tags=[("component", "optics"), ("interface", "{#XCVRNAME}")]),
                          item("Transceiver {#XCVRNAME}: temperature high warning", f"{P}.20.1.1.10.{{#SNMPINDEX}}",
                               xk("temp.hwarn"), "FLOAT", "1h", "°C", pre=[("MULTIPLIER", ["0.01"])],
                               desc="The module's own threshold; absent when it carries none.",
                               tags=[("component", "optics"), ("interface", "{#XCVRNAME}")]),
                          item("Transceiver {#XCVRNAME}: threshold state", f"{P}.20.1.1.18.{{#SNMPINDEX}}", xk("state"),
                               vmap="PRISMA-ROUTING-MIB::prXcvrState",
                               desc="The worst level any reading of the module stands at against its own thresholds.",
                               tags=[("component", "optics"), ("interface", "{#XCVRNAME}")]),
                      ], desc="PRISMA-ROUTING-MIB's prXcvrTable (#590): a row per port with a cage, under its IF-MIB ifIndex.",
                      tprotos=[
                          trig(f"{xc} and (last({ref(xk('temp'))})>{{$PRISMA.XCVR.TEMP.MAX:\"{{#XCVRNAME}}\"}}"
                               f" or last({ref(xk('temp'))})>last({ref(xk('temp.hwarn'))}))",
                               "Prisma Routing: transceiver {#XCVRNAME} temperature is high",
                               "WARNING", "Above {$PRISMA.XCVR.TEMP.MAX:\"{#XCVRNAME}\"} C or the module's own high warning: airflow, or a module failing.",
                               tags=[("scope", "performance")]),
                          trig(f"{xc} and last({ref(xk('state'))})=3",
                               "Prisma Routing: transceiver {#XCVRNAME} has a reading past its alarm threshold",
                               "HIGH", "A temperature, voltage, bias, TX or RX power past the module's own alarm level: 'show interface {#XCVRNAME} transceiver' says which.",
                               tags=[("scope", "availability")]),
                      ]))
    lx = lambda s: f"prisma.xcvr.lane.{s}[{{#SNMPINDEX}}]"
    lc = '{$PRISMA.XCVR.CONTROL:"{#XCVRLANE}"}=1'
    rules.append(rule("Transceiver lane discovery", f"discovery[{{#XCVRLANE}},{P}.20.2.1.2]",
                      "prisma.xcvr.lane.discovery", [
                          item("Transceiver {#XCVRLANE}: RX power", f"{P}.20.2.1.3.{{#SNMPINDEX}}", lx("rx"),
                               "FLOAT", units="dBm", pre=[("MULTIPLIER", ["0.01"])],
                               desc="-40 dBm is no light: the receiver reads 0 mW.",
                               tags=[("component", "optics"), ("interface", "{#XCVRLANE}")]),
                          item("Transceiver {#XCVRLANE}: RX power level", f"{P}.20.2.1.8.{{#SNMPINDEX}}", lx("rx.level"),
                               vmap="PRISMA-ROUTING-MIB::prXcvrLaneLevel",
                               tags=[("component", "optics"), ("interface", "{#XCVRLANE}")]),
                          item("Transceiver {#XCVRLANE}: RX power low warning", f"{P}.20.2.1.6.{{#SNMPINDEX}}",
                               lx("rx.lwarn"), "FLOAT", "1h", "dBm", pre=[("MULTIPLIER", ["0.01"])],
                               desc="The module's own threshold; absent when it carries none.",
                               tags=[("component", "optics"), ("interface", "{#XCVRLANE}")]),
                          item("Transceiver {#XCVRLANE}: TX power", f"{P}.20.2.1.9.{{#SNMPINDEX}}", lx("tx"),
                               "FLOAT", units="dBm", pre=[("MULTIPLIER", ["0.01"])],
                               tags=[("component", "optics"), ("interface", "{#XCVRLANE}")]),
                          item("Transceiver {#XCVRLANE}: laser bias", f"{P}.20.2.1.15.{{#SNMPINDEX}}", lx("bias"),
                               "FLOAT", units="A", pre=[("MULTIPLIER", ["0.000001"])],
                               tags=[("component", "optics"), ("interface", "{#XCVRLANE}")]),
                      ], desc="PRISMA-ROUTING-MIB's prXcvrLaneTable (#590): a row per optical lane, ifIndex.lane - one for an SFP, four for a QSFP.",
                      tprotos=[
                          trig(f"{lc} and (last({ref(lx('rx.level'))})=3 or last({ref(lx('rx.level'))})=5"
                               f" or last({ref(lx('rx'))})<{{$PRISMA.XCVR.RX.MIN:\"{{#XCVRLANE}}\"}})",
                               "Prisma Routing: transceiver {#XCVRLANE} RX power is low",
                               "WARNING", "Below the module's own low warning (or {$PRISMA.XCVR.RX.MIN:\"{#XCVRLANE}\"} dBm): a dirty or damaged fibre or connector, a long span, or the far end's laser. Compare with the far end's TX power.",
                               tags=[("scope", "performance")]),
                          trig(f"{lc} and (last({ref(lx('rx.level'))})=2 or last({ref(lx('rx.level'))})=4"
                               f" or last({ref(lx('rx'))})>{{$PRISMA.XCVR.RX.MAX:\"{{#XCVRLANE}}\"}})",
                               "Prisma Routing: transceiver {#XCVRLANE} RX power is high",
                               "WARNING", "Above the module's own high warning (or {$PRISMA.XCVR.RX.MAX:\"{#XCVRLANE}\"} dBm): a short span with no attenuator overdrives the receiver.",
                               tags=[("scope", "performance")]),
                      ]))

    # ------------------------------------------------ ENTITY-MIB (#496)
    # The same modules as the transceiver rules above, in the standard MIBs:
    # an inventory, and the sensors' values for graphs. Collected without
    # triggers - one alarm per event, and the optical alarms are the
    # transceiver rules', which carry the per-port macros - except a module
    # swapped, which nothing above says.
    E = "1.3.6.1.2.1.47.1.1.1.1"
    ek = lambda s: f"entity.module.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("Module discovery (ENTITY-MIB)",
                      f"discovery[{{#ENTNAME}},{E}.7,{{#ENTCLASS}},{E}.5]",
                      "entity.module.discovery", [
                          item("Module {#ENTNAME}: vendor", f"{E}.12.{{#SNMPINDEX}}", ek("vendor"),
                               "CHAR", "1h", pre=[HEART], tags=[("component", "inventory")]),
                          item("Module {#ENTNAME}: model", f"{E}.13.{{#SNMPINDEX}}", ek("model"),
                               "CHAR", "1h", pre=[HEART], tags=[("component", "inventory")]),
                          item("Module {#ENTNAME}: revision", f"{E}.8.{{#SNMPINDEX}}", ek("revision"),
                               "CHAR", "1h", pre=[HEART], tags=[("component", "inventory")]),
                          item("Module {#ENTNAME}: serial number", f"{E}.11.{{#SNMPINDEX}}", ek("serial"),
                               "CHAR", "1h", pre=[HEART], tags=[("component", "inventory")],
                               desc="A new value is a module swapped in the cage.", proto=True, triggers=[
                                   trig(f"change({ref(ek('serial'))})=1",
                                        "Prisma Routing: module {#ENTNAME} was replaced", "INFO",
                                        "Its serial number changed: a module swapped in the cage. Close it once the swap is known.",
                                        tags=[("scope", "notice")], manual=True)]),
                      ], filt=[("{#ENTCLASS}", "^9$", None)],
                      desc="ENTITY-MIB (RFC 6933): the pluggable modules, entPhysicalClass module(9) - a row per port with a module in its cage, entPhysicalIndex ifIndex x 1000 + 1."))
    S = "1.3.6.1.2.1.99.1.1.1"
    sk = lambda s: f"entity.sensor.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("Sensor discovery (ENTITY-SENSOR-MIB)",
                      f"discovery[{{#SENSORNAME}},{E}.7,{{#SENSORTYPE}},{S}.1,"
                      f"{{#SENSORPRECISION}},{S}.3,{{#SENSORUNITS}},{S}.6]",
                      "entity.sensor.discovery", [
                          item("Sensor {#SENSORNAME}", f"{S}.4.{{#SNMPINDEX}}", sk("value"), "FLOAT",
                               units="{#SENSORUNITS}",
                               pre=[("JAVASCRIPT", ["return value / Math.pow(10, {#SENSORPRECISION});"])],
                               desc="entPhySensorValue over 10 to the entPhySensorPrecision, in entPhySensorUnitsDisplay's units: an optical power is in uW (RFC 3433 has no dBm).",
                               tags=[("component", "sensor")]),
                          item("Sensor {#SENSORNAME}: status", f"{S}.5.{{#SNMPINDEX}}", sk("status"),
                               delay="5m", vmap="ENTITY-SENSOR-MIB::EntitySensorStatus",
                               tags=[("component", "sensor")]),
                          item("Sensor {#SENSORNAME}: threshold level", f"{P}.21.1.1.5.{{#SNMPINDEX}}",
                               sk("level"), vmap="PRISMA-ROUTING-MIB::prXcvrLaneLevel",
                               desc="PRISMA-ROUTING-MIB's prSensorLevel: where the value stands against the module's own thresholds; absent when it carries none.",
                               tags=[("component", "sensor")]),
                      ], filt=[("{#SENSORTYPE}", "^[0-9]+$", None)],
                      desc="ENTITY-SENSOR-MIB (RFC 3433): each module's temperature, voltage, bias, TX and RX power. The alarms are on the transceiver discoveries above, which read the same modules."))

    # ------------------------------------------------ the IETF/IEEE tables
    rules.append(rule("NATV2 instance discovery", "discovery[{#NATV2ALIAS},1.3.6.1.2.1.234.2.1.1.2]",
                      "natv2.instance.discovery", [
                          item("NATV2 {#NATV2ALIAS}: address map entries", "1.3.6.1.2.1.234.2.1.1.7.{#SNMPINDEX}",
                               "natv2.instance.addrmap[{#SNMPINDEX}]", tags=[("component", "cgnat")]),
                          item("NATV2 {#NATV2ALIAS}: port map entries", "1.3.6.1.2.1.234.2.1.1.8.{#SNMPINDEX}",
                               "natv2.instance.portmap[{#SNMPINDEX}]", tags=[("component", "cgnat")]),
                          item("NATV2 {#NATV2ALIAS}: port map failure drops per second",
                               "1.3.6.1.2.1.234.2.1.1.16.{#SNMPINDEX}", "natv2.instance.failures.rate[{#SNMPINDEX}]",
                               "FLOAT", units="pps", pre=[CPS], tags=[("component", "cgnat")]),
                      ], desc="NATV2-MIB (RFC 7659), one instance per pool. The alarms are on the pool discovery above, which names the pool the same way."))
    rules.append(rule("VRRPv3 router discovery", "discovery[{#VRRPV3STATUS},1.3.6.1.2.1.207.1.1.1.1.6]",
                      "vrrpv3.discovery", [
                          item("VRRPv3 {#SNMPINDEX}: status", "1.3.6.1.2.1.207.1.1.1.1.6.{#SNMPINDEX}",
                               "vrrpv3.status[{#SNMPINDEX}]", vmap="VRRPV3-MIB::vrrpv3OperationsStatus",
                               tags=[("component", "ha")]),
                          item("VRRPv3 {#SNMPINDEX}: priority", "1.3.6.1.2.1.207.1.1.1.1.7.{#SNMPINDEX}",
                               "vrrpv3.priority[{#SNMPINDEX}]", tags=[("component", "ha")]),
                      ], desc="VRRPV3-MIB (RFC 6527), indexed ifIndex.VRID.family. The alarms are on the VRRP router discovery above."))
    lk = lambda s: f"lag.port.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("LACP member discovery", "discovery[{#LAGAGG},1.2.840.10006.300.43.1.2.1.1.12]",
                      "lag.port.discovery", [
                          item("LACP member ifIndex {#SNMPINDEX}: attached to aggregator",
                               "1.2.840.10006.300.43.1.2.1.1.13.{#SNMPINDEX}", lk("attached"),
                               tags=[("component", "lacp")], triggers=[
                                   trig(f"last({ref(lk('attached'))})=0",
                                        "Prisma Routing: LACP member ifIndex {#SNMPINDEX} is detached from its bundle",
                                        "WARNING", tags=[("scope", "availability")])], proto=True),
                          item("LACP member ifIndex {#SNMPINDEX}: mux state",
                               "1.2.840.10006.300.43.1.2.3.1.3.{#SNMPINDEX}", lk("mux"),
                               vmap="IEEE8023-LAG-MIB::dot3adAggPortDebugMuxState", tags=[("component", "lacp")]),
                          item("LACP member ifIndex {#SNMPINDEX}: LACPDUs received per second",
                               "1.2.840.10006.300.43.1.2.2.1.1.{#SNMPINDEX}", lk("lacpdu.rx.rate"), "FLOAT",
                               pre=[CPS], tags=[("component", "lacp")]),
                      ], desc="IEEE8023-LAG-MIB, a row per member under its IF-MIB ifIndex."))

    # ------------------------------------------------ routing
    gk = lambda s: f"bgp.peer.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("BGP peer discovery (IPv4, BGP4-MIB)", "discovery[{#BGPPEER},1.3.6.1.2.1.15.3.1.7]",
                      "bgp.peer.discovery", [
                          item("BGP peer {#BGPPEER}: state", "1.3.6.1.2.1.15.3.1.2.{#SNMPINDEX}", gk("state"),
                               vmap="BGP4-MIB::bgpPeerState", tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                          item("BGP peer {#BGPPEER}: administrative status", "1.3.6.1.2.1.15.3.1.3.{#SNMPINDEX}",
                               gk("admin"), delay="5m", vmap="BGP4-MIB::bgpPeerAdminStatus",
                               tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                          item("BGP peer {#BGPPEER}: remote AS", "1.3.6.1.2.1.15.3.1.9.{#SNMPINDEX}", gk("as"),
                               delay="1h", tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                          item("BGP peer {#BGPPEER}: updates received per second", "1.3.6.1.2.1.15.3.1.10.{#SNMPINDEX}",
                               gk("in.updates"), "FLOAT", pre=[CPS], tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                          item("BGP peer {#BGPPEER}: updates sent per second", "1.3.6.1.2.1.15.3.1.11.{#SNMPINDEX}",
                               gk("out.updates"), "FLOAT", pre=[CPS], tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                          item("BGP peer {#BGPPEER}: established for", "1.3.6.1.2.1.15.3.1.16.{#SNMPINDEX}",
                               gk("uptime"), units="uptime", tags=[("component", "bgp"), ("peer", "{#BGPPEER}")]),
                      ], tprotos=[trig(f"last({ref(gk('state'))})<>6 and last({ref(gk('admin'))})=2",
                                       "Prisma Routing: BGP peer {#BGPPEER} is not established",
                                       "AVERAGE", tags=[("scope", "availability")])]))
    g2 = lambda s: f"bgp4v2.peer.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("BGP peer discovery (BGP4V2-MIB)",
                      "discovery[{#BGP4V2STATE},1.3.6.1.3.5.1.1.2.1.13,{#BGP4V2DESCR},1.3.6.1.3.5.1.1.2.1.14]",
                      "bgp4v2.peer.discovery", [
                          item("BGP peer {#SNMPINDEX} ({#BGP4V2DESCR}): state", "1.3.6.1.3.5.1.1.2.1.13.{#SNMPINDEX}",
                               g2("state"), vmap="BGP4-MIB::bgpPeerState", tags=[("component", "bgp")]),
                          item("BGP peer {#SNMPINDEX} ({#BGP4V2DESCR}): administrative status",
                               "1.3.6.1.3.5.1.1.2.1.12.{#SNMPINDEX}", g2("admin"), delay="5m",
                               vmap="BGP4V2-MIB::bgp4V2PeerAdminStatus", tags=[("component", "bgp")]),
                          item("BGP peer {#SNMPINDEX} ({#BGP4V2DESCR}): remote AS", "1.3.6.1.3.5.1.1.2.1.10.{#SNMPINDEX}",
                               g2("as"), delay="1h", tags=[("component", "bgp")]),
                      ], filt=[("{#SNMPINDEX}", "{$BGP4V2.INDEX.MATCHES}", None)],
                      desc="BGP4V2-MIB (draft-ietf-idr-bgp4-mibv2, experimental arc), which bgpd indexes instance.type.address with no length byte: type 2 is IPv6, and an unnumbered session peers on a link-local address. IPv6 only by default: BGP4-MIB has the IPv4 peers.",
                      tprotos=[trig(f"last({ref(g2('state'))})<>6 and last({ref(g2('admin'))})=2",
                                    "Prisma Routing: BGP peer {#SNMPINDEX} ({#BGP4V2DESCR}) is not established",
                                    "AVERAGE", tags=[("scope", "availability")])]))
    ok = lambda s: f"ospf.nbr.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("OSPF neighbour discovery", "discovery[{#OSPFNBR},1.3.6.1.2.1.14.10.1.3]",
                      "ospf.nbr.discovery", [
                          item("OSPF neighbour {#OSPFNBR}: state", "1.3.6.1.2.1.14.10.1.6.{#SNMPINDEX}", ok("state"),
                               vmap="OSPF-MIB::ospfNbrState", tags=[("component", "ospf")], triggers=[
                                   trig(f"max({ref(ok('state'))},3m)<8 and last({ref(ok('state'))})<>4",
                                        "Prisma Routing: OSPF neighbour {#OSPFNBR} is not full",
                                        "AVERAGE", "twoWay is left alone: it is the steady state between two routers that are not DR or BDR.",
                                        tags=[("scope", "availability")])], proto=True),
                          item("OSPF neighbour {#OSPFNBR}: events per second", "1.3.6.1.2.1.14.10.1.7.{#SNMPINDEX}",
                               ok("events.rate"), "FLOAT", pre=[CPS], tags=[("component", "ospf")]),
                      ]))
    o3 = lambda s: f"ospfv3.nbr.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("OSPFv3 neighbour discovery", "discovery[{#OSPFV3STATE},1.3.6.1.2.1.191.1.9.1.8]",
                      "ospfv3.nbr.discovery", [
                          item("OSPFv3 neighbour {#SNMPINDEX}: state", "1.3.6.1.2.1.191.1.9.1.8.{#SNMPINDEX}",
                               o3("state"), vmap="OSPF-MIB::ospfNbrState", tags=[("component", "ospf")], triggers=[
                                   trig(f"max({ref(o3('state'))},3m)<8 and last({ref(o3('state'))})<>4",
                                        "Prisma Routing: OSPFv3 neighbour {#SNMPINDEX} is not full",
                                        "AVERAGE", "Indexed ifIndex.instance.router-id. twoWay is left alone.",
                                        tags=[("scope", "availability")])], proto=True),
                      ]))
    ik = lambda s: f"isis.adj.{s}[{{#SNMPINDEX}}]"
    rules.append(rule("IS-IS adjacency discovery", "discovery[{#ISISSYSID},1.3.6.1.2.1.138.1.6.1.1.6]",
                      "isis.adj.discovery", [
                          item("IS-IS adjacency {#SNMPINDEX}: state", "1.3.6.1.2.1.138.1.6.1.1.2.{#SNMPINDEX}",
                               ik("state"), vmap="ISIS-MIB::isisISAdjState", tags=[("component", "isis")], triggers=[
                                   trig(f"last({ref(ik('state'))})<>3",
                                        "Prisma Routing: IS-IS adjacency {#SNMPINDEX} is not up",
                                        "AVERAGE", tags=[("scope", "availability")])], proto=True),
                      ]))

    tpl = {
        "uuid": uid("template"),
        "template": T,
        "name": T,
        "description": ("Prisma Routing (#514): the dataplane's interfaces, the CGNAT, the connection records, "
                        "HA, LACP, BGP, OSPF and IS-IS, the ports' transceivers and modules, PPPoE and its RADIUS servers, over SNMP v2c or v3 (v1 is refused). "
                        "Print it with the box's enterprise number filled in: `show snmp zabbix-template`."),
        "vendor": {"name": "Prisma Routing", "version": "0.146"},
        "groups": [{"name": "Templates/Network devices"}],
        "items": items,
        "discovery_rules": rules,
        "tags": [{"tag": "class", "value": "network"}, {"tag": "target", "value": "prisma-routing"}],
        "macros": [{"macro": m, "value": v, "description": d} for m, v, d in MACROS],
        "valuemaps": [{"uuid": uid("valuemap", n), "name": n,
                       "mappings": [{"value": str(a), "newvalue": b} for a, b in vals]}
                      for n, vals in VALUEMAPS.items()],
    }
    return {"zabbix_export": {"version": "7.0",
                              "template_groups": [{"uuid": uid("group"), "name": "Templates/Network devices"}],
                              "templates": [tpl]}}


if __name__ == "__main__":
    json.dump(build(), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
