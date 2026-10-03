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
    (["ping"], "text", "OperationalService.Show", "a typed probe is phase 11"),
    (["traceroute"], "text", "OperationalService.Show", "a typed probe is phase 11"),
    (["mtr"], "text", "OperationalService.Show", "a typed probe is phase 11"),
    (["monitor", "interface"], "typed", "TelemetryService.Subscribe",
     "interface counters as a stream; GetHistory for the past day"),
    (["monitor", "cgnat", "logging"], "none", "-",
     "GetLogging gives the streams' state, not the events: phase 4"),
    (["clear"], "none", "-", "phase 3"),
    (["rollback"], "typed", "ConfigService.Rollback, then Commit", ""),
    (["save"], "typed", "ConfigService.Save", ""),
    (["system", "upgrade"], "none", "-", "phase 3, with a progress stream"),
    (["system", "reboot"], "none", "-", "phase 3"),
    (["system", "shutdown"], "none", "-", "phase 3"),
    (["system", "dataplane"], "none", "-", "phase 3"),
    (["exit"], "n/a", "-", "the session's"),
    # The reads with a typed rpc. Everything else under `show` is text.
    (["show", "version"], "typed", "OperationalService.GetVersion", ""),
    (["show", "system", "uptime"], "typed", "OperationalService.GetSystem",
     "boot, dataplane, control-plane and VTY start times"),
    (["show", "system", "memory"], "typed", "OperationalService.GetSystem",
     "memory_total/used/hugepages bytes"),
    (["show", "system", "cpu"], "typed", "OperationalService.GetSystem",
     "worker_load per worker; per-core figures are text (phase 7)"),
    (["show", "interface", "utilization"], "typed",
     "TelemetryService.Subscribe / GetHistory", "rates from the counter stream"),
    (["show", "interface"], "typed", "OperationalService.ListInterfaces", ""),
    (["show", "cgnat", "pool"], "typed", "OperationalService.ListPools", ""),
    (["show", "cgnat", "subscriber"], "typed", "OperationalService.GetSubscriber", ""),
    (["show", "cgnat", "session"], "typed",
     "OperationalService.GetSessionsSummary", ""),
    (["show", "cgnat", "logging"], "typed", "OperationalService.GetLogging", ""),
    (["show", "bgp"], "typed", "OperationalService.ListBgpNeighbors",
     "per-neighbor detail beyond the summary is text (phase 6)"),
    (["show", "ospf", "neighbor"], "typed", "OperationalService.ListOspfNeighbors", ""),
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
             "filter", "alg", "vrf", "outside-vrf", "mode"),
    "cgnat": ("logging",),
}
TYPED_CONFIG_API = "ConfigService.GetConfig / SetConfig / DeleteConfig"

#: The routing half (#558, phase 3). Static routes and BGP neighbours - what
#: a family says about one included - are read AND written as fields; the
#: rest below is read as fields by GetConfig and written as lines through
#: Edit. A statement of these levels no field takes is still read back, in
#: its block's `other`, verbatim - so it stays `text` here.
TYPED_CONFIG.update({"router static": ("<prefix>",)})
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
    "system": (("*", "system", False),),
    "system memory": (("*", "system", False),),
    "system cpu": (("*", "system", False),),
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
BLOCK_ENTRIES = {"config": ("nat", "rpki", "router", "interface", "system",
                            "policy-options", "user", "snmp-user",
                            "route-map"),
                 # `vrf <name>` (FRR's block) vs `vrf <name> table <id>`:
                 # the second is a statement, area vrfs (phase 4).
                 "nat": ("cgnat",)}


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
            return ("typed", TYPED_CONFIG_API,
                    "and as lines through Edit; GetSchema/ValidateLines check "
                    "a line (0.149.22)" if level == "router static" else
                    "and as lines through Edit; GetSchema/ValidateLines check "
                    "a line (0.149.20)")
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
    return out


# ------------------------------------------------------------ the gaps

#: What prisma-web asked for that is not a VTY command either, so the walk
#: cannot find it: it is listed by hand, and goes away when a phase ships.
NOT_IN_THE_VTY = [
    ("clear interface counters (one or all)", "phase 3: in the VTY and the API at once"),
    ("clear a CGNAT subscriber's sessions / its block", "phase 3: in the VTY and the API at once"),
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
3. **Actions** (item 6). `OperationalService.Clear {target: interface
   counters | all counters | cgnat subscriber <ip> | bgp/ospf/isis ...}` -
   the counters and the subscriber clears added to the VTY in the same
   release; `SystemService.Reboot`, `Shutdown`, `RestartDataplane` (an
   administrator, an explicit confirmation field), and
   `SystemService.Upgrade(version) returns (stream UpgradeProgress {phase,
   percent, message, api_gap_expected_s, version})`.
4. **Monitors as streams** (item 7). `TelemetryService.WatchLogging(filter)
   returns (stream LogEvent {time, kind, subscriber, public, port_start,
   port_end, pool_name, subscriber_id, repeats_held})` - the observation
   ring `monitor cgnat logging` reads (#556) - plus the per-stream delivery
   lines.
5. **CGNAT reads** (the rest of `show cgnat`). Typed `ListBlocks` (per
   thread: in use, allocations, releases, refusals), `GetDrops` (counter
   per reason, both directions), `GetCgnatMemory`, `ListTop`,
   `LookupTranslation(public, port, at)` - the lawful-intercept question,
   `GetIdentity`, `VerifyHistory`, `ListAlgs`, `GetForward`, `GetHaPeer`.
6. **Routing detail.** BGP neighbor detail (prefixes received/accepted,
   timers, capabilities, last error), OSPF and IS-IS interfaces, verbose
   routes.
7. **System detail.** `show hardware` (NICs, PCI, NUMA, queues), per-core
   CPU, `show logging` as a stream of the box's own log, `show snmp mib` and
   the Zabbix template as downloads.
8. **Accounts and SNMP users**, typed reads (accounts, roles, whether a key
   or password is set, SNMP users and their levels); edits stay lines.
9. **Routing configuration typed reads**: static routes, BGP neighbors and
   address families, OSPF, IS-IS, RPKI.
10. **Policy configuration typed reads**: prefix-lists, policy-statements
    and terms as structure (ListPolicies already returns what is compiled).
11. **Probes as streams**: ping, traceroute, mtr as `ProbeService` with one
    message per reply/hop, instead of the finished screen through Show.

Every row this table marks `text` is reachable today; `none` rows become
reachable in the phase their note names.
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
    for cls in ("typed", "text", "none", "n/a", "UNCLASSIFIED"):
        if count.get(cls):
            lines.append(f"| {cls} | {count[cls]} |")
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
