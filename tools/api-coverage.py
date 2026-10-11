#!/usr/bin/env python3
"""
api-coverage.py [--check]: which VTY command the management API reaches, and how (#558).

Walks the VTY's own grammar - TREE for the operational prompt and every
configuration level, and the block grammars the routing levels use
(prismash.block_grammar's tables) - and classifies every command path it
finds against prisma.v1:

  typed  an rpc returns it as structured fields (or takes it as such);
  text   reachable, but only as screen text through OperationalService.Show,
         or as a configuration line through ConfigService.Edit, read back as
         text through GetRunning / ShowCandidate;
  none   not reachable through the API at all;
  n/a    belongs to the interactive session itself (exit, top, up), or is
         excluded on purpose (a shell).

Writes docs/api-coverage.md, or with --check compares it with what the tree
says now and exits 1 when they differ or when a path has no classification:
test/test_api_coverage.py runs that, so a VTY command added without saying
how the API reaches it fails `make test`.

The classification is RULES, not a list of commands: a new `show` is text
until somebody gives it a typed rpc and a rule says so, and a new top-level
word the rules do not know is UNCLASSIFIED, which is the failure.
"""
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src", "prismash"))
import prismash as P  # noqa: E402
import typedcfg  # noqa: E402

DOC = os.path.join(REPO, "docs", "api-coverage.md")

#: Words every level offers that are the session's, not the box's.
SESSION_WORDS = ("exit", "top", "up")
#: Words every configuration level repeats; classified once, where they
#: belong: `show` under the operational prompt, `commit`/`rollback` at
#: the top of the configuration.
SHARED_WORDS = ("show", "commit", "rollback")

#: The configuration levels the prompt can enter, in the order a reader
#: meets them: (level, how it is entered, the path EditRequest.path names,
#: the grammar, who may change it). The block grammars are
#: prismash.block_grammar's - the same tables `?` answers from.
#: Every configuration level, from the product's own table
#: (typedcfg.config_modes), which GetSchema answers from too: the document and
#: the API cannot list different modes. (level, how it is entered, the path
#: as this document prints it, its grammar, the roles that change it.)
CONFIG_LEVELS = [m[:5] for m in typedcfg.config_modes(P)]

# ------------------------------------------------------------ the rules

#: Operational paths, longest prefix first wins: (prefix words, class, api,
#: note). `show` paths not named here are text through Show.
OPERATIONAL_RULES = [
    (["configure"], "typed", "ConfigService.OpenCandidate", ""),
    (["test", "policy"], "typed", "OperationalService.TestPolicy", ""),
    (["start", "shell"], "n/a", "-",
     "never through the API: a shell is every capability at once"),
    # #558, phase 7: the probes, streamed - a ProbeEvent per line the tool
    # prints, its reply or hop read, then its end.
    (["ping"], "typed", "OperationalService.Ping (a stream)",
     "a reply per message, then the summary"),
    (["traceroute"], "typed", "OperationalService.Traceroute (a stream)",
     "a hop per message"),
    (["mtr"], "typed", "OperationalService.Mtr (a stream)",
     "the report's hops, at the end of the run"),
    (["monitor", "interface"], "typed", "TelemetryService.Subscribe",
     "interface counters as a stream; GetHistory for the past day"),
    # #588 phase 3: the box's record as it is written.
    (["monitor", "log"], "typed", "OperationalService.WatchEvents (a stream)",
     "a BoxEvent each as it is recorded, ListEvents' filters; `last` or "
     "`since` what is sent first (#588 phase 3)"),
    (["monitor", "cgnat", "logging"], "typed", "TelemetryService.WatchLogging",
     "the block events as a stream of LogEvent, the same filters; delivered "
     "in 0.149.25"),
    (["clear"], "typed", "OperationalService.Clear",
     "a ClearRequest target per clear; delivered in 0.149.25"),
    (["rollback"], "typed", "ConfigService.Rollback, then Commit", ""),
    (["save"], "typed", "ConfigService.Save", ""),
    (["system", "upgrade"], "typed", "SystemService.Upgrade",
     "a stream of UpgradeProgress, ending before the API restarts; "
     "GetUpgradeStatus after; delivered in 0.149.25"),
    (["system", "reboot"], "typed", "SystemService.Reboot",
     "refused while unsaved or inside a confirm window unless force"),
    (["system", "shutdown"], "typed", "SystemService.Shutdown",
     "refused while unsaved or inside a confirm window unless force"),
    (["system", "dataplane"], "typed", "SystemService.RestartDataplane",
     "run on the host, detached; the reply when it finished"),
    (["exit"], "n/a", "-", "the session's"),
    # The reads with a typed rpc. Everything else under `show` is text.
    (["show", "version"], "typed", "OperationalService.GetVersion", ""),
    (["show", "system", "uptime"], "typed", "OperationalService.GetSystem",
     "boot, dataplane, control-plane and VTY start times, and why each "
     "started (#588)"),
    (["show", "system", "memory"], "typed", "OperationalService.GetSystem",
     "memory_total/used/hugepages bytes"),
    (["show", "system", "logging"], "typed", "OperationalService.GetSystem",
     "logging: each destination and whether it is reached; logging_text "
     "(#588 phase 3)"),
    (["show", "system", "hardware"], "typed", "OperationalService.GetSystem",
     "unsupported_sfp_configured/booted (#574)"),
    (["show", "system", "management"], "typed", "OperationalService.GetSystem",
     "management: the VRF configured and in effect; management_text (#580)"),
    (["show", "system", "cpu"], "typed", "OperationalService.GetSystem",
     "worker_load per worker; `cores`: cpu_topology, cpu_usage per CPU "
     "and cpu_cores_text, GetHistory cpus (#598)"),
    # #596: the box's own tuning, judged, with the host's pinning.
    (["show", "system", "tuning"], "typed",
     "OperationalService.GetTuningAdvice",
     "facts, findings, recommendations, snippets and sizing; "
     "host_topology is the paste (#596)"),
    # #590: each port's module and its optical levels.
    (["show", "interface", "transceiver"], "typed",
     "OperationalService.ListTransceivers",
     "and `show interface <name> transceiver` by its names (#590)"),
    (["show", "interface", "utilization"], "typed",
     "TelemetryService.Subscribe / GetHistory", "rates from the counter stream"),
    (["show", "interface"], "typed", "OperationalService.ListInterfaces", ""),
    (["show", "cgnat", "pool"], "typed", "OperationalService.ListPools", ""),
    # #572: the NAT64 half of `show cgnat`, refusing what is not NAT64. Its
    # answers are typed where `show cgnat`'s are - ListPools, GetSubscriber
    # and LookupTranslation answer a NAT64 pool and subscriber as well.
    (["show", "nat64", "pool"], "typed", "OperationalService.ListPools",
     "the pools whose level is nat64 (#572)"),
    (["show", "nat64", "subscriber"], "typed",
     "OperationalService.GetSubscriber",
     "an IPv6 address: the NAT64 subscriber (#572)"),
    (["show", "nat64", "translation"], "typed",
     "OperationalService.LookupTranslation",
     "a NAT64 pool's public address answers its /56 (#572)"),
    (["show", "cgnat", "subscriber"], "typed", "OperationalService.GetSubscriber", ""),
    (["show", "cgnat", "session"], "typed",
     "OperationalService.GetSessionsSummary", ""),
    (["show", "cgnat", "logging"], "typed", "OperationalService.GetLogging", ""),
    # #558, phase 6: the rest of `show cgnat`, each its screen read into
    # fields and carried beside them verbatim.
    (["show", "cgnat", "translation"], "typed",
     "OperationalService.LookupTranslation", "now or any instant, as the screen says"),
    (["show", "cgnat", "top"], "typed", "OperationalService.ListTop", ""),
    (["show", "cgnat", "drops"], "typed", "OperationalService.GetDrops", ""),
    (["show", "configuration", "pending"], "typed",
     "OperationalService.GetPendingConfiguration",
     "and the health service prisma.v1.Configuration (#575)"),
    (["show", "cgnat", "memory"], "typed", "OperationalService.GetCgnatMemory",
     "heap and session figures in bytes and counts"),
    (["show", "cgnat", "blocks"], "typed", "OperationalService.ListBlocks", ""),
    (["show", "cgnat", "identity"], "typed", "OperationalService.GetIdentity",
     "the count and RADIUS' counters, never an identity"),
    (["show", "cgnat", "history", "verify"], "typed",
     "OperationalService.VerifyHistory", ""),
    (["show", "cgnat", "alg"], "typed", "OperationalService.ListAlgs", ""),
    (["show", "cgnat", "forward"], "typed", "OperationalService.ListForwards", ""),
    (["show", "cgnat", "ha", "peer"], "typed", "OperationalService.GetHaStatus",
     "peer_path: an absolute path, read as the account, a regular file of 1 "
     "MiB at most"),
    (["show", "cgnat", "ha"], "typed", "OperationalService.GetHaStatus",
     "the peer copy at its default path"),
    # #559 phase 2: the sessions typed, from the reading the screen prints;
    # WatchBfd streams their state changes.
    (["show", "bfd"], "typed", "OperationalService.ListBfdSessions",
     "and WatchBfd for state changes (#559)"),
    (["show", "bgp"], "typed", "OperationalService.ListBgpNeighbors",
     "per-neighbor detail beyond the summary is text (phase 6)"),
    (["show", "ospf", "neighbor"], "typed", "OperationalService.ListOspfNeighbors", ""),
    # #558, phase 7: the rest of the screens, each drawn from the facts the
    # rpc carries, the screen verbatim beside them.
    (["show", "ospf", "interface"], "typed", "OperationalService.ListOspfInterfaces",
     "both MTUs and the stall"),
    (["show", "isis", "interface"], "typed", "OperationalService.ListIsisInterfaces",
     "the circuits and the dataplane's IS-IS punt"),
    (["show", "hardware"], "typed", "OperationalService.GetHardware", ""),
    (["show", "logging"], "typed", "OperationalService.GetRoutingLogging",
     "FRR's logging per daemon"),
    # #588: the box's own record of events, its filters and its screen.
    (["show", "log"], "typed", "OperationalService.ListEvents",
     "the same filters, `kind` too, with each event's subject and reason; "
     "the restart causes are also in GetSystem (#588)"),
    (["show", "snmp", "mib"], "typed", "OperationalService.GetSnmpFile (KIND_MIB)",
     "the file, with the enterprise number in it"),
    (["show", "snmp", "zabbix-template"], "typed",
     "OperationalService.GetSnmpFile (KIND_ZABBIX_TEMPLATE)",
     "the JSON Zabbix 7.0 imports"),
    (["show", "isis", "adjacency"], "typed",
     "OperationalService.ListIsisAdjacencies", ""),
    (["show", "lacp"], "typed", "OperationalService.ListBundles", ""),
    (["show", "policy"], "typed",
     "OperationalService.ListPolicies / GetPolicyCounters / TestPolicy", ""),
    (["show", "ip"], "typed", "RouteService.ListRoutes / GetRouteSummary", ""),
    (["show", "route"], "typed", "RouteService.ListRoutes", ""),
    (["show", "running-config"], "typed", "ConfigService.GetRunning",
     "the configuration as text, with its revision"),
    (["show", "configuration", "compare"], "typed", "ConfigService.Compare", ""),
    (["show", "this"], "typed", "ConfigService.ShowCandidate", ""),
    (["show", "rollback"], "typed", "ConfigService.ListRevisions", ""),
    (["show"], "text", "OperationalService.Show", ""),
]

#: Paths at the top of the configuration with their own rpc.
CONFIG_RULES = [
    (["commit", "check"], "typed", "ConfigService.Check", ""),
    (["commit", "confirmed"], "typed",
     "ConfigService.Commit (confirm) / Confirm / GetPendingConfirm",
     "the window read back from the box (0.149.18)"),
    (["commit", "comment"], "typed", "ConfigService.Commit (comment)", ""),
    (["commit", "remap"], "typed", "ConfigService.Commit (acknowledge_remap)", ""),
    (["commit", "cutoff"], "typed", "ConfigService.Commit (acknowledge_cutoff)", ""),
    (["commit", "diverge"], "typed",
     "ConfigService.Commit (acknowledge_diverge)", ""),
    (["commit"], "typed", "ConfigService.Commit",
     "refusals carry Diagnostics.reason (0.149.18)"),
    (["rollback"], "typed", "ConfigService.Rollback", ""),
    # #585: an edit OF the candidate, the session's like `top`, and not a
    # statement in it. Edit takes the line as the prompt does, at the level
    # `path` names; SetConfig takes a whole candidate, which is what a client
    # computing the same edit sends.
    (["replace"], "n/a", "-",
     "an edit of the candidate, not a statement: ConfigService.Edit takes "
     "the line, SetConfig the whole result (#585)"),
    (["rename"], "n/a", "-",
     "an edit of the candidate, not a statement: ConfigService.Edit takes "
     "the line, SetConfig the whole result (#585)"),
]

#: What a typed rpc reads back of a configuration level, though the level
#: is edited as lines: the note a UI needs to know it can read it typed.
TYPED_READS = {
    "interface (bundle)": "the shared statements are typed through "
                          "GetConfig/SetConfig; member and lacp are lines",
    "cgnat": "logging is typed through GetConfig/SetConfig; GetLogging is "
             "its runtime state",
}

#: Statements read and written as fields by ConfigService.GetConfig /
#: SetConfig / DeleteConfig (#558, phase 2), by level and first words; the
#: line form through Edit stays for each of them too.
TYPED_CONFIG = {
    "interface (port)": ("description", "vlan", "vrf", "linux-name",
                         "mac-address", "cgnat", "ipv4", "ipv6 address",
                         "ipv6 nd", "shutdown", "mtu", "urpf"),
    "interface (bundle)": ("description", "vlan", "vrf", "linux-name",
                           "mac-address",
                           "cgnat", "ipv4", "ipv6 address", "ipv6 nd",
                           "shutdown", "mtu", "urpf"),
    "pool": ("prefix-inside", "prefix-outside", "block-size", "ports",
             "policy", "timeout", "max-blocks", "initial-allocation",
             "block-stride", "reserve", "bypass", "utilisation-alarm",
             "max-sessions", "max-session-rate", "tcp-mss", "forward",
             "filter", "alg", "vrf", "outside-vrf", "mode",
             # #589: PoolConfig.prefix_list_inside / prefix_list_outside.
             "prefix-list-inside", "prefix-list-outside"),
    # #572: a NAT64 pool's, as Nat64PoolConfig - the pool's less the bypass,
    # plus its own two.
    "nat64 pool": ("prefix-inside", "prefix-outside", "block-size", "ports",
                   "policy", "timeout", "max-blocks", "initial-allocation",
                   "block-stride", "reserve", "utilisation-alarm",
                   "max-sessions", "max-session-rate", "tcp-mss", "forward",
                   "filter", "alg", "vrf", "outside-vrf", "mode",
                   "subscriber-length", "nat64-prefix"),
    "cgnat": ("logging",),
}
#: Statements one level's pool grammar offers and that level refuses at
#: commit (#572): the same Pool grammar serves both levels.
REFUSED_AT_LEVEL = {
    "pool": {"subscriber-length": "NAT64's: refused under cgnat since #572, "
                                  "typed under nat64 > pool",
             "nat64-prefix": "NAT64's: refused under cgnat since #572, typed "
                             "under nat64 > pool"},
    "nat64 pool": {"bypass": "refused on a NAT64 pool for good (#485)",
                   "prefix-list-inside": "a CGNAT pool's: a NAT64 pool takes "
                                         "no prefix-list (#589)",
                   "prefix-list-outside": "a CGNAT pool's: a NAT64 pool "
                                          "takes no prefix-list (#589)"},
}
TYPED_CONFIG_API = "ConfigService.GetConfig / SetConfig / DeleteConfig"

#: The routing half (#558, phase 3). Static routes and BGP neighbours - what
#: a family says about one included - are read AND written as fields; the
#: rest below is read as fields by GetConfig and written as lines through
#: Edit. A statement of these levels no field takes is still read back, in
#: its block's `other`, verbatim - so it stays `text` here.
TYPED_CONFIG.update({"router static": ("<prefix>",)})
#: The `bfd` block (#559 phase 3): sessions and profiles, read and written as
#: fields (BfdPeerConfig, BfdProfileConfig), every timer of their body too.
_BFD_BODY = ("detect-multiplier", "receive-interval", "transmit-interval",
             "echo-mode", "echo", "passive-mode", "minimum-ttl", "shutdown")
TYPED_CONFIG.update({"bfd": ("peer", "profile"),
                     "bfd peer": _BFD_BODY + ("profile",),
                     "bfd profile": _BFD_BODY})
#: `neighbor <address> <word> ...` statements that have a field, by level:
#: the others (capability, remove-private-AS, advertise) are read back
#: verbatim in the neighbour's `other` and stay `text`.
TYPED_NEIGHBOR_WORDS = {
    "bgp neighbor": ("remote-as", "description", "password", "update-source",
                     "ebgp-multihop", "timers", "bfd", "shutdown",
                     "peer-group"),
    "address-family neighbor": ("activate", "route-map", "prefix-list",
                                "next-hop-self", "soft-reconfiguration",
                                "maximum-prefix", "default-originate"),
}
TYPED_ROUTING_READS = {
    "router bgp": ("bgp router-id", "neighbor", "network", "aggregate-address",
                   "redistribute", "address-family"),
    "address-family": ("neighbor", "network", "aggregate-address",
                       "redistribute"),
    "router ospf": ("ospf router-id", "network", "area", "passive-interface",
                    "default-information", "redistribute"),
    "router ospf6": ("ospf6 router-id", "area", "redistribute"),
    "router isis": ("net", "is-type", "metric-style", "redistribute"),
    "interface (port)": ("ip ospf", "ipv6 ospf6", "ip router isis",
                         "ipv6 router isis", "isis"),
    # An operator VRF's FRR block: its static routes, read by phase 3 as
    # `static` with their `vrf` (phase 4 lists the block as a mode).
    "vrf": ("ip route", "ipv6 route"),
    # A bundle's FRR `interface` block is read the same way (phase 4).
    "interface (bundle)": ("ip ospf", "ipv6 ospf6", "ip router isis",
                           "ipv6 router isis", "isis"),
}
TYPED_ROUTING_API = "ConfigService.GetConfig (fields) / Edit (lines)"
#: Which typedcfg.SETTING_KEYS a routing level's knobs are read by (#558
#: phase 4): what phase 3 left in `other`, as key and value.
SETTING_KIND = {"router bgp": "bgp", "address-family": "family",
                "bgp neighbor": "neighbor",
                "address-family neighbor": "neighbor_family",
                "router ospf": "ospf", "router ospf6": "ospf",
                "router isis": "isis"}


#: The rest of the configuration (#558, phase 4): by level, the statements
#: GetConfig reads as fields - (first words, area, written as fields too).
#: "*" is every statement of the level. What is read typed but written as
#: lines is so because it carries a secret (a community, a password, a
#: passphrase, a key) or has no field-shaped edit yet; the note says which.
TYPED_PHASE4 = {
    "config": (("hostname", "system", True), ("dns server", "system", True),
               ("ssh", "system", True), ("api", "system", True),
               ("snmp enabled", "system", True),
               ("snmp listen-address", "system", True),
               ("snmp port", "system", True),
               ("snmp enterprise-number", "system", True),
               ("snmp community", "system", False),
               ("snmp trap-host", "system", False),
               ("vrf", "vrfs", True),
               ("ip prefix-list", "frr_lists", False),
               ("ipv6 prefix-list", "frr_lists", False),
               ("bgp community-list", "frr_lists", False),
               ("bgp large-community-list", "frr_lists", False),
               ("bgp as-path", "frr_lists", False),
               ("ip protocol", "routing_attachments", False),
               ("ipv6 protocol", "routing_attachments", False),
               ("fib-policy", "routing_attachments", False)),
    "cgnat": (("timeout", "cgnat_globals", False),
              ("block-holddown", "cgnat_globals", False),
              ("identity", "cgnat_globals", False),
              ("session-table", "cgnat_globals", False),
              ("memory", "cgnat_globals", False),
              ("pool", "pools", True), ("interface", "interfaces", True)),
    # #572: NAT64's level holds its pools, area nat64.
    "nat64": (("pool", "nat64", True),),
    "system": (("*", "system", False),),
    "system memory": (("*", "system", False),),
    "system cpu": (("*", "system", False),),
    "system hardware": (("*", "system", False),),
    "system logging": (("*", "system", False),),
    "system management": (("*", "system", False),),
    "system dataplane": (("*", "system", False),),
    "user": (("*", "users", False),),
    "snmp-user": (("*", "snmp_users", False),),
    "policy-options": (("*", "policy", False),),
    "prefix-list": (("*", "policy", False),),
    "policy-statement": (("*", "policy", False),),
    "term": (("*", "policy", False),),
    "route-map": (("*", "route_maps", False),),
    "rpki": (("*", "rpki", False),),
    "interface (port)": (("vrrp", "vrrp", False),),
    "interface (bundle)": (("vrrp", "vrrp", False), ("member", "bundles", False),
                           ("lacp", "bundles", False),
                           ("load-balance", "bundles", False)),
}
#: Why a phase-4 area is written as lines, by area.
PHASE4_LINES_WHY = {
    "system": "a community or trap host carries a secret; system > memory, "
              "cpu and dataplane take effect at the next boot",
    "users": "an account carries a password and keys",
    "snmp_users": "an SNMP user carries passphrases",
    "policy": "a policy is a program: written as lines, read as fields",
    "route_maps": "written as lines, read as fields",
    "frr_lists": "written as lines, read as fields",
    "routing_attachments": "written as lines, read as fields",
    "rpki": "an SSH cache names key files",
    "cgnat_globals": "a RADIUS identity source carries a secret",
    "vrrp": "written as lines, read as fields",
    "bundles": "written as lines, read as fields",
}


#: The words that ENTER a block rather than state anything (#558 phase 4):
#: a client reaches each through EditRequest.path, and what is inside is
#: classified line by line under the block's own level.
BLOCK_ENTRIES = {"config": ("nat", "rpki", "bfd", "router", "interface",
                            "system",
                            "policy-options", "user", "snmp-user",
                            "route-map"),
                 # `vrf <name>` (FRR's block) vs `vrf <name> table <id>`:
                 # the second is a statement, area vrfs (phase 4).
                 "nat": ("cgnat", "nat64")}


def classify_phase4(level, stmt):
    """(class, api, note) when phase 4 types `stmt` at `level`, else None."""
    for first, area, written in TYPED_PHASE4.get(level, ()):
        if first == "*" or stmt[:len(first.split())] == first.split():
            if written:
                return ("typed", TYPED_CONFIG_API,
                        f"area {area}: read and written as fields "
                        f"(0.149.24)")
            return ("typed", "ConfigService.GetConfig (fields) / Edit "
                    "(lines)", f"area {area}: read as fields since "
                    f"0.149.24; written as lines - "
                    f"{PHASE4_LINES_WHY[area]}")
    return None


def classify_operational(words):
    for prefix, cls, api, note in OPERATIONAL_RULES:
        if words[:len(prefix)] == prefix:
            return cls, api, note
    return "UNCLASSIFIED", "-", "no rule in tools/api-coverage.py"


def classify_config(level, words):
    if level == "config":
        for prefix, cls, api, note in CONFIG_RULES:
            if words[:len(prefix)] == prefix:
                return cls, api, note
    if words[0] in SESSION_WORDS:
        return "n/a", "-", "the session's"
    # A grammar key can be two words ("neighbor <address>"): compare word
    # by word, or a neighbour's typed statements never match (they were
    # listed as text in 0.149.22's document for that reason).
    words = " ".join(words).split()
    stmt = words[1:] if words[0] == "no" else words
    if stmt and stmt[0] in REFUSED_AT_LEVEL.get(level, {}):
        return "n/a", "-", REFUSED_AT_LEVEL[level][stmt[0]]
    if words[0] != "no" and len(words) <= 3 \
            and words[0] in BLOCK_ENTRIES.get(level, ()) \
            and (words[0] != "interface" or level != "cgnat"):
        return ("typed", "ConfigService.Edit (path)",
                "enters the block; its statements are classified under it, "
                "and GetConfig reads them by area")
    p4 = classify_phase4(level, stmt)
    if p4:
        return p4
    for first in TYPED_CONFIG.get(level, ()):
        if first.startswith("<") and stmt and stmt[0].startswith("<"):
            return ("typed", TYPED_CONFIG_API,
                    "and as lines through Edit; GetSchema/ValidateLines check "
                    "a line (0.149.22)")
        if stmt[:len(first.split())] == first.split():
            since = ("#559 phase 3" if level.startswith("bfd") else
                     "0.149.22" if level == "router static" else "0.149.20")
            return ("typed", TYPED_CONFIG_API,
                    "and as lines through Edit; GetSchema/ValidateLines check "
                    f"a line ({since})")
    if words == ["exit-address-family"]:
        return "n/a", "-", "closes the address-family block"
    kind = SETTING_KIND.get(level)
    if kind:
        body = stmt[2:] if stmt[:1] == ["neighbor"] and kind.startswith(
            "neighbor") else stmt
        for key in typedcfg.SETTING_KEYS[kind]:
            if body[:len(key.split())] == key.split():
                return ("typed", TYPED_ROUTING_API,
                        "read as a key/value in the block's `settings` since "
                        "0.149.24; written as lines")
    if level in TYPED_NEIGHBOR_WORDS and stmt[:1] == ["neighbor"]:
        if len(stmt) > 2 and stmt[2] in TYPED_NEIGHBOR_WORDS[level]:
            return ("typed", TYPED_CONFIG_API,
                    "and as lines through Edit; GetSchema/ValidateLines check "
                    "a line (0.149.22)")
        return ("text", "ConfigService.Edit (lines) / GetRunning",
                "read back verbatim in the neighbour's `other` (GetConfig)")
    for first in TYPED_ROUTING_READS.get(level, ()):
        if stmt[:len(first.split())] == first.split():
            return ("typed", TYPED_ROUTING_API,
                    "read as fields since 0.149.22; written as lines")
    if level in TYPED_ROUTING_READS:
        return ("text", "ConfigService.Edit (lines) / GetRunning",
                "read back verbatim in its block's `other` (GetConfig)")
    return ("text", "ConfigService.Edit (lines) / GetRunning",
            TYPED_READS.get(level, ""))


# ------------------------------------------------------------ the walk

def walk(node, prefix, out, depth=0):
    """Every command path under `node`: (words, argument hint)."""
    for key, val in node.items():
        sub = val[1] if isinstance(val, tuple) and len(val) > 1 else None
        if isinstance(sub, dict) and sub and depth < 7:
            walk(sub, prefix + [key], out, depth + 1)
        else:
            out.append((prefix + [key], sub if isinstance(sub, str) else ""))


def rows():
    """[(level, words, hint, class, api, note, role)] for every command."""
    out = []
    paths = []
    walk(P.TREE["operational"], [], paths)
    for words, hint in paths:
        cls, api, note = classify_operational(words)
        role = ("administrator" if words[:1] in (["system"], ["clear"],
                                                 ["start"])
                else "every account")
        out.append(("operational", words, hint, cls, api, note, role))
    for level, _enter, _path, grammar, role in CONFIG_LEVELS:
        paths = []
        walk(grammar, [], paths)
        for words, hint in paths:
            if words[0] in SHARED_WORDS and not (
                    level == "config" and words[0] in ("commit", "rollback")):
                continue
            cls, api, note = classify_config(level, words)
            out.append((level, words, hint, cls, api, note, role))
    # `replace` and `rename` (#585): words of every level, in a tree of their
    # own that no level's grammar carries - listed once, from the top, as
    # the Shell offers them there.
    top = types.SimpleNamespace(edit_scope=lambda: [],
                                RENAME_TO=P.Shell.RENAME_TO)
    paths = []
    walk(P.Shell.edit_tree(top), [], paths)
    role = next(r for lvl, _e, _p, _g, r in CONFIG_LEVELS if lvl == "config")
    for words, hint in paths:
        cls, api, note = classify_config("config", words)
        out.append(("config", words, hint, cls, api, note, role))
    return out


# ------------------------------------------------------------ the gaps

#: What prisma-web asked for that is not a VTY command either, so the walk
#: cannot find it: it is listed by hand, and goes away when a phase ships.
NOT_IN_THE_VTY = [
]

PHASES = """\
## Phases

In the order prisma-web asked for (the #558 comment of 2026-10-02), each a
patch release with its lab coverage in lab/api-grpc.sh and its
docs/clients.md entry. Proto shapes are proposals; names follow the domain
grouping rule (#533).

1. **Commit and candidate facts** (prisma-web items 1-3) - *delivered in
   0.149.18*. `Diagnostics.reason` on every refused ConfigService call and
   `CheckResponse.reason` (stale base with the running revision it moved to,
   permission, candidate expired/unknown, limit, the three gates with their
   items, confirm pending, grammar, validation, apply failed, usage);
   `ConfigService.GetPendingConfirm`; `ConfigService.ListCandidates`, and
   `expires_at` / `last_activity` / `idle_seconds` on the candidate replies.
2. **Typed configuration for what the UI edits** (items 4-5) - *delivered
   in 0.149.20*.
   `ConfigService.GetConfig(area, source=running|candidate)` returning
   `InterfaceConfig {name, description, mtu, shutdown, ipv4[], ipv6[],
   cgnat_role, vrf, linux_name}`, `PoolConfig {name, prefix_inside[],
   prefix_outside[], block_size, mode, policy, max_blocks, alg flags}`,
   `LoggingConfig {udp {collector, src, port, enabled}, tcp {...}, syslog
   {collector, port, enterprise, facility, severity, configured,
   never_enabled}}`; typed edits as `ConfigService.Set{Interface,Pool,
   Logging}` that write lines into the candidate (so the text form stays the
   one truth); and `ConfigService.GetSchema` - the grammar `?` answers from,
   for validation and completion in a form.
3. **Actions** (item 6) - *delivered in 0.149.25*, with `clear interface
   counters` and `clear cgnat subscriber` added to the VTY.
   `OperationalService.Clear {target: interface
   counters | all counters | cgnat subscriber <ip> | bgp/ospf/isis ...}` -
   the counters and the subscriber clears added to the VTY in the same
   release; `SystemService.Reboot`, `Shutdown`, `RestartDataplane` (an
   administrator, an explicit confirmation field), and
   `SystemService.Upgrade(version) returns (stream UpgradeProgress {phase,
   percent, message, api_gap_expected_s, version})`.
4. **Monitors as streams** (item 7) - *delivered in 0.149.25*.
   `TelemetryService.WatchLogging(filter)
   returns (stream LogEvent {time, kind, subscriber, public, port_start,
   port_end, pool_name, subscriber_id, repeats_held})` - the observation
   ring `monitor cgnat logging` reads (#556) - plus the per-stream delivery
   lines.
5. **CGNAT reads** (the rest of `show cgnat`) - *delivered as #558's sixth
   phase*. `LookupTranslation(public, port)` - the lawful-intercept
   question, with whether the answer holds for any instant or only now;
   `ListTop`, `GetDrops` (counter per reason, both nodes), `GetCgnatMemory`,
   `ListBlocks` (per worker: in use, allocations, releases, refusals,
   pinned, lent), `GetIdentity`, `VerifyHistory`, `ListAlgs`,
   `ListForwards`, `GetHaStatus`. Each reads the VTY's own screen through
   the same Shell and carries it verbatim beside the fields. `show cgnat ha
   peer <file>` is `GetHaStatusRequest.peer_path` since #558's last phase:
   an absolute path, read as the account, a regular file only.
6. **Routing detail.** OSPF and IS-IS interfaces - *delivered as #558's
   seventh phase*: `ListOspfInterfaces` (both MTUs and the stall between
   them) and `ListIsisInterfaces` (the circuits and the dataplane's IS-IS
   punt). Still to come: BGP neighbor detail (prefixes received/accepted,
   timers, capabilities, last error) and verbose routes.
7. **System detail** - *delivered as #558's seventh phase*: `GetHardware`
   (the NICs Linux has, PCI, driver, link, the default route's card, what
   the dataplane holds), `GetRoutingLogging` (`show logging`: FRR's logging
   configuration per daemon) and `GetSnmpFile` (`show snmp mib` and the
   Zabbix template as files to download). Per-core CPU is still to come.
8. **Accounts and SNMP users**, typed reads (accounts, roles, whether a key
   or password is set, SNMP users and their levels); edits stay lines.
9. **Routing configuration typed reads**: static routes, BGP neighbors and
   address families, OSPF, IS-IS, RPKI.
10. **Policy configuration typed reads**: prefix-lists, policy-statements
    and terms as structure (ListPolicies already returns what is compiled).
11. **Probes as streams** - *delivered as #558's seventh phase*, on
    OperationalService rather than a service of their own: `Ping`,
    `Traceroute` and `Mtr` stream a `ProbeEvent` per line the tool prints,
    with its reply or hop read, then `end`; the VTY's own validation, its
    count ceiling and its deadline. `Show` still answers them as text.

No VTY command is `text` or `none` since #558's last phase: each is an rpc
with fields, or excluded on purpose (`n/a`). `Show` still answers every
screen as text, for a console.
"""


def render():
    rs = rows()
    count = {}
    for r in rs:
        count[r[3]] = count.get(r[3], 0) + 1
    lines = [
        "<!-- Generated by tools/api-coverage.py from the VTY's grammar; do",
        "     not edit by hand. test/test_api_coverage.py fails when it is",
        "     stale or a command has no classification. -->",
        "# What the management API reaches of the VTY",
        "",
        "| | |",
        "|---|---|",
        "| **Who** | Generated for whoever builds a client of the management API (prisma-web first) and whoever adds a VTY command. |",
        "| **What** | Every command and configuration statement of the VTY, and how prisma.v1 reaches it: **typed** (an rpc with fields), **text** (the screen through `OperationalService.Show`, or lines through `ConfigService.Edit`), **none** (not reachable), **n/a** (the session's own, or excluded on purpose). |",
        "| **Where** | Generated by `tools/api-coverage.py` from the same tables `?` answers from; checked by `test/test_api_coverage.py`. The API reference is [docs/api.md](api.md); per-release changes for clients are [docs/clients.md](clients.md). |",
        "| **When** | Regenerated with every release that adds a command or an rpc (#558). |",
        "",
        "> **This document's format is a contract** until clients have moved to",
        "> `ConfigService.GetSchema` (0.149.20), which answers the same grammar",
        "> typed. prisma-web parses the statement syntax below (`<A.B.C.D/M>`,",
        "> `<1-N>`, `a|b`, `[optional]`) and each level's \"Entered with ...",
        "> `EditRequest.path` ... changed by\" line, so neither changes shape",
        "> without a docs/clients.md entry saying so first.",
        "",
        "## Summary",
        "",
        "| Class | Commands |",
        "|---|---|",
    ]
    # typed, text and none always, a zero included: #558's measure is that
    # the last two read 0, and a row left out says nothing.
    for cls in ("typed", "text", "none", "n/a", "UNCLASSIFIED"):
        if count.get(cls) or cls in ("typed", "text", "none"):
            lines.append(f"| {cls} | {count.get(cls, 0)} |")
    lines += [
        "",
        "Configuration statements are counted one per grammar path. Every one",
        "is written as the line it is at the prompt, at the level `path`",
        "names (`ConfigService.Edit`); `typed` says GetConfig also reads it as",
        "fields (#558), and SetConfig writes it where the API column names it.",
        "Statements the VTY hands to FRR unread (`<- not checked`) are",
        "reachable the same way: Edit passes them on as the VTY would.",
        "",
        "## Not reachable today",
        "",
        "| Command | Phase |",
        "|---|---|",
    ]
    for level, words, hint, cls, api, note, role in rs:
        if cls == "none":
            lines.append(f"| `{' '.join(words)}` ({level}) | {note} |")
    for what, phase in NOT_IN_THE_VTY:
        lines.append(f"| {what} (not a VTY command either) | {phase} |")
    lines += ["", PHASES.rstrip(), "", "## Operational prompt", "",
              "| Command | Class | API | Who | Notes |",
              "|---|---|---|---|---|"]
    for level, words, hint, cls, api, note, role in rs:
        if level != "operational":
            continue
        cmd = " ".join(words) + (f" {hint}" if hint else "")
        lines.append(f"| `{cmd}` | {cls} | {api} | {role} | {note} |")
    for level, enter, path, _g, role in CONFIG_LEVELS:
        lines += ["", f"## Configuration: {level}", "",
                  f"Entered with `{enter}`; `EditRequest.path` = `{path}`; "
                  f"changed by: {role}.", ""]
        note = TYPED_READS.get(level)
        if note:
            lines += [f"*{note}.*", ""]
        lines += ["| Statement | Class | API | Notes |", "|---|---|---|---|"]
        for lvl, words, hint, cls, api, nt, _r in rs:
            if lvl != level:
                continue
            stmt = " ".join(words) + (f" {hint}" if hint else "")
            stmt = stmt.replace("|", "\\|")
            nt = "" if nt == note else nt
            lines.append(f"| `{stmt}` | {cls} | {api} | {nt} |")
    return "\n".join(lines) + "\n"


def main():
    text = render()
    if "--check" in sys.argv:
        bad = [r for r in rows() if r[3] == "UNCLASSIFIED"]
        for r in bad:
            print(f"UNCLASSIFIED: {r[0]}: {' '.join(r[1])}")
        try:
            current = open(DOC).read()
        except OSError:
            current = ""
        if current != text:
            print("docs/api-coverage.md is stale: run python3 tools/api-coverage.py")
            return 1
        return 1 if bad else 0
    with open(DOC, "w") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
