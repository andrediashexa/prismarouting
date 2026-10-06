#!/usr/bin/env python3
"""
The management API's contract, read as text (#532): what a client in another
repository compiles against, held to the rules its header promises.

Read without protoc, which `make test`'s machine does not have; the lab
(lab/api-grpc.sh) and the management image's build compile it for real.
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PROTO = os.path.join(ROOT, "api", "prisma", "v1", "prisma.proto")
fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")
    else:
        print(f"  [ ok ] {msg}")


# The words tonic cannot take as a field or message name without a raw
# identifier - a contract that needs r#type in every client is a worse one.
RUST = {"as", "break", "const", "continue", "crate", "else", "enum", "extern",
        "false", "fn", "for", "if", "impl", "in", "let", "loop", "match",
        "mod", "move", "mut", "pub", "ref", "return", "self", "static",
        "struct", "super", "trait", "true", "type", "unsafe", "use", "where",
        "while", "async", "await", "dyn", "abstract", "become", "box", "do",
        "final", "macro", "override", "priv", "typeof", "unsized", "virtual",
        "yield", "try"}

text = open(PROTO).read()
body = re.sub(r"//[^\n]*", "", text)

print("=== the package and its promise ===")
check(re.search(r"^package prisma\.v1;", body, re.M) is not None,
      "the package is prisma.v1, the version clients pin")
check('syntax = "proto3";' in body, "proto3")
for words in ("only ever ADDED", "never renumbered", "prisma.v2",
              "prisma-diagnostics-bin", "Common Name"):
    check(words in text, f"the header says {words!r}")

print("=== every message numbers its fields once ===")
messages = {}
for m in re.finditer(r"\bmessage (\w+) \{", body):
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(body[i], 0)
        i += 1
    inner = body[m.end():i - 1]
    # Nested messages and enums are read on their own.
    flat = re.sub(r"\b(message|enum) \w+ \{[^{}]*\}", "", inner)
    messages[m.group(1)] = re.findall(r"^\s*(?:optional |repeated )?[\w.]+ (\w+) = (\d+);",
                                      flat, re.M)
check(len(messages) > 40, f"{len(messages)} messages read")
# A name defined twice is a contract protoc refuses, and only protoc - which
# this machine has not got - would say so: #588 added a second `Event` and
# the first sign was lab/api-grpc.sh with no stubs at all.
# At column 0: a nested `enum Kind` is scoped to its message, and several
# messages have one.
_names = re.findall(r"^(?:message|enum) (\w+) \{", body, re.M)
_twice = sorted({n for n in _names if _names.count(n) > 1})
check(not _twice, f"no message or enum is defined twice: {_twice}")
for name, fields in messages.items():
    nums = [int(n) for _, n in fields]
    check(len(nums) == len(set(nums)), f"{name}: no field number used twice {nums}")
    check(all(n >= 1 for n in nums), f"{name}: field numbers start at 1")
    for f, _ in fields:
        check(f not in RUST, f"{name}.{f} is not a Rust keyword")
    check(name.lower() not in RUST, f"message {name} is not a Rust keyword")

print("=== the services the issue asks for ===")
services = dict(re.findall(r"service (\w+) \{(.*?)\n\}", body, re.S))
want = {
    "ConfigService": ["OpenCandidate", "Edit", "ShowCandidate", "Compare",
                      "Check", "Commit", "Confirm", "Rollback", "Save",
                      "CloseCandidate", "GetRunning", "ListRevisions",
                      "GetPendingConfirm", "ListCandidates",
                      # #558 phase 2: typed configuration.
                      "GetConfig", "SetConfig", "DeleteConfig", "GetSchema",
                      "ValidateLines"],
    "OperationalService": ["GetVersion", "GetSystem", "ListInterfaces",
                           "ListPools", "GetSubscriber", "GetSessionsSummary",
                           "ListBgpNeighbors", "ListOspfNeighbors",
                           "ListIsisAdjacencies", "ListVrrp", "ListBundles",
                           "ListPolicies", "GetPolicyCounters",
                           "TestPolicy",
                           "Show",
                           # #555: the CGNAT log export's state.
                           "GetLogging",
                           # #558 phase 5: the VTY's clears.
                           "Clear",
                           # #558 phase 6: the CGNAT's screens, typed.
                           "LookupTranslation", "ListTop", "GetDrops",
                           # #575: what the boot left out for a card.
                           "GetPendingConfiguration",
                           "GetCgnatMemory", "ListBlocks", "GetIdentity",
                           "VerifyHistory", "ListAlgs", "ListForwards",
                           "GetHaStatus",
                           # #558 phase 7: the rest of the screens, and the
                           # probes streamed.
                           "ListOspfInterfaces", "ListIsisInterfaces",
                           "GetHardware", "GetRoutingLogging", "GetSnmpFile",
                           "Ping", "Traceroute", "Mtr", "ListBfdSessions", "WatchBfd",
                           # #588: show log.
                           "ListEvents"],
    "TelemetryService": ["Subscribe", "GetHistory",
                         # #558 phase 5: monitor cgnat logging.
                         "WatchLogging"],
    # #533: new subjects, new services; their rpcs are the subject's.
    "RouteService": ["ListRoutes", "GetRouteSummary"],
    "SessionService": ["WhoAmI"],
    # #558 phase 5: the box itself, a subject of its own.
    "SystemService": ["Reboot", "Shutdown", "RestartDataplane", "Upgrade",
                      "GetUpgradeStatus"],
}
for svc, rpcs in want.items():
    got = re.findall(r"rpc (\w+)\(", services.get(svc, ""))
    check(got == rpcs, f"{svc}: {got}")
check("returns (stream TelemetryUpdate)" in body,
      "Subscribe streams its updates")
check("returns (stream HistorySample)" in body,
      "GetHistory streams a sample a message: a day of every interface is more "
      "than one message should carry")
check(set(services) == set(want), f"no service beyond these: {sorted(services)}")

print("=== the health protocol, gRPC's own (#533) ===")
HEALTH = os.path.join(ROOT, "api", "health", "v1", "health.proto")
htext = re.sub(r"//[^\n]*", "", open(HEALTH).read())
check(re.search(r"^package grpc\.health\.v1;", htext, re.M) is not None,
      "grpc.health.v1 on the wire, so a stock health client works")
check(re.findall(r"rpc (\w+)\(", htext) == ["Check", "Watch"],
      "Check and Watch, as gRPC defines them")
check(re.search(r"ServingStatus status = 1;", htext) is not None
      and all(w in htext for w in ("UNKNOWN = 0", "SERVING = 1", "NOT_SERVING = 2",
                                   "SERVICE_UNKNOWN = 3")),
      "the status enum, number for number")
check(not os.path.isdir(os.path.join(ROOT, "api", "grpc")),
      "not under api/grpc: stubs named grpc would shadow the gRPC library")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
