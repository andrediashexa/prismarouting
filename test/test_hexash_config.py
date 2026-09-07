#!/usr/bin/env python3
"""
The candidate configuration model: which changes recreate a pool.

`Config.delta_to` decides, for every field of a pool, whether changing it
rebuilds the pool on the dataplane or is applied in place. That decision is
not a detail of the diff - it is the difference between a setting that reaches
the box and a setting that reaches the box after disconnecting every
subscriber on the pool (#69), and between an operator being warned that a
change re-maps subscribers and being surprised by it (#23).

Both directions are wrong in their own way:

  - a mapping field classified as in-place is sent to a dataplane that cannot
    apply it that way, and the pool keeps its old mapping while the running
    configuration says otherwise;
  - an in-place field classified as mapping drops every session on the pool to
    turn one ALG off.

The table below states the classification for every field a pool has, and the
last case asserts that the TABLE IS COMPLETE - a field added later and not
classified here fails, rather than silently defaulting to whatever the diff
happens to do with it.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


def base_pool(name="p"):
    p = config.Pool(name)
    p.inside = ["100.64.0.0/22"]
    p.outside = ["200.150.0.16/30"]
    return p


def cfg_with(pool):
    c = config.Config()
    c.pools[pool.name] = pool
    return c


# name -> (how to change it, which delta bucket must report it)
#
# "pool_replace" means every session on the pool ends. Anything else means the
# pool keeps carrying traffic while the setting changes underneath it.
MUTATIONS = {
    # --- the mapping. Changing any of these changes which public address and
    # --- port block a subscriber gets, so the pool is rebuilt.
    "inside":       (lambda p: p.inside.append("100.64.4.0/22"), "pool_replace"),
    "outside":      (lambda p: p.outside.append("45.170.16.0/28"), "pool_replace"),
    "block_size":   (lambda p: setattr(p, "block_size", "1024"), "pool_replace"),
    "ports":        (lambda p: setattr(p, "ports", "2048-65535"), "pool_replace"),
    "policy":       (lambda p: setattr(p, "policy", "blocked"), "pool_replace"),
    "mode":         (lambda p: setattr(p, "mode", "dynamic"), "pool_replace"),
    # A pool that starts serving one VRF stops serving the others, so the set
    # of subscribers it maps changes even though the arithmetic does not.
    "vrf":          (lambda p: setattr(p, "vrf", "1"), "pool_replace"),
    "outside_vrf":  (lambda p: setattr(p, "outside_vrf", "2"), "pool_replace"),

    # --- settings the dataplane changes under a worker barrier, in place.
    "timeouts":     (lambda p: p.timeouts.update({"udp": 900}), "settings_set"),
    "max_blocks":   (lambda p: setattr(p, "max_blocks", 2), "settings_set"),
    "tcp_mss":      (lambda p: setattr(p, "tcp_mss", "1400"), "settings_set"),
    "util_alarm":   (lambda p: setattr(p, "util_alarm", 80), "settings_set"),
    "filter":       (lambda p: setattr(p, "filter", "address-dependent"),
                     "settings_set"),
    "algs":         (lambda p: p.algs.update({"rtsp": False}), "settings_set"),

    "max_sessions": (lambda p: setattr(p, "max_sessions", 2000), "limits_set"),
    "max_rate":     (lambda p: setattr(p, "max_rate", 50), "limits_set"),

    "bypass_src":   (lambda p: p.bypass_src.append("100.64.1.0/24"),
                     "bypass_set"),
    "bypass_dst":   (lambda p: p.bypass_dst.append("10.0.0.0/8"), "bypass_set"),

    "forwards":     (lambda p: p.forwards.append(("tcp", 8080, "100.64.0.5",
                                                  80)), "fwd_add"),
}

# Fields that are not settings at all: the pool's identity, and the tables the
# mutations above reach through.
NOT_A_SETTING = {"name"}


print("=== which changes rebuild a pool, and which do not ===")
for field, (mutate, bucket) in sorted(MUTATIONS.items()):
    before = cfg_with(base_pool())
    after = cfg_with(base_pool())
    mutate(after.pools["p"])

    d = before.delta_to(after)
    replaced = [p.name for p in d["pool_replace"]]

    if bucket == "pool_replace":
        check(replaced == ["p"],
              f"changing {field} must rebuild the pool, got replace={replaced}")
    else:
        check(replaced == [],
              f"changing {field} must NOT rebuild the pool "
              f"(every session on it would end), got replace={replaced}")
        check(bool(d[bucket]),
              f"changing {field} must be reported in {bucket}, "
              f"which was empty")

print("  every field classified above behaves as classified")

print("\n=== the table covers every field a pool has ===")
# The assertion that survives the next feature. Without it, a field added to
# Pool and forgotten here is classified by accident: whatever lines() happens
# to do with it decides whether an operator loses their subscribers.
have = {k for k in vars(base_pool()) if not k.startswith("_")}
classified = set(MUTATIONS) | NOT_A_SETTING
missing = sorted(have - classified)
extra = sorted(classified - have - NOT_A_SETTING)
check(not missing,
      f"these pool fields are not classified as mapping or in-place: {missing}")
check(not extra, f"these entries name fields a pool no longer has: {extra}")
print(f"  {len(have)} fields, {len(missing)} unclassified")

print("\n=== a change that changes nothing asks for nothing ===")
# A diff that reports work for an unchanged configuration is a commit that
# rebuilds pools for no reason, and an operator who learns to ignore the
# warning that says so.
same = cfg_with(base_pool())
d = same.delta_to(cfg_with(base_pool()))
busy = {k: v for k, v in d.items() if v}
check(not busy, f"an identical configuration must ask for nothing: {busy}")
print(f"  buckets reporting work: {len(busy)}")

print("\n=== a new pool is added, not replaced ===")
d = config.Config().delta_to(cfg_with(base_pool()))
check([p.name for p in d["pool_add"]] == ["p"], "a new pool must be added")
check(d["pool_replace"] == [], "and not also replaced")
# A pool being created carries its own settings, so a separate call would be
# a second write of what the first one already said.
check(not d["settings_set"],
      f"a new pool needs no separate settings call: {d['settings_set']}")
check(not d["limits_set"],
      f"nor a separate limits call: {d['limits_set']}")
print("  add=1, replace=0, no follow-up calls")

print("\n=== a pool being rebuilt re-sends what it loses ===")
# The other side of the rule above: a pool that is recreated arrives empty of
# forwards, so every forward the candidate asks for is an addition. Treating
# it as unchanged leaves the operator's inbound ports silently gone.
before = cfg_with(base_pool())
before.pools["p"].forwards.append(("tcp", 8080, "100.64.0.5", 80))
after = copy.deepcopy(before)
after.pools["p"].block_size = "1024"
d = before.delta_to(after)
check([p.name for p in d["pool_replace"]] == ["p"], "the pool is rebuilt")
check([f for _, f in d["fwd_add"]] == [("tcp", 8080, "100.64.0.5", 80)],
      f"and its forwards are re-sent: {d['fwd_add']}")
print(f"  forwards re-sent: {len(d['fwd_add'])}")

print("\n=== removing a pool is a removal, not a rebuild ===")
d = cfg_with(base_pool()).delta_to(config.Config())
check(d["pool_remove"] == ["p"], "the pool must be removed")
check(d["pool_replace"] == [], "and not replaced")

print("\n=== lines(in_place=False) is the mapping and only the mapping ===")
# What delta_to compares. Anything that leaks into it rebuilds the pool.
rich = base_pool()
for field, (mutate, bucket) in MUTATIONS.items():
    if bucket != "pool_replace":
        mutate(rich)
check(rich.lines(in_place=False) == base_pool().lines(in_place=False),
      "no in-place setting may appear in the mapping half of a pool:\n"
      f"    {rich.lines(in_place=False)}\n"
      f"    {base_pool().lines(in_place=False)}")
check(len(rich.lines()) > len(rich.lines(in_place=False)),
      "and the in-place settings must appear in the full text")
print(f"  mapping lines: {len(rich.lines(in_place=False))}, "
      f"full: {len(rich.lines())}")

print("\n=== the API arguments a pool produces ===")
p = base_pool()
p.timeouts["udp"] = 900
p.algs["rtsp"] = False
p.tcp_mss = "auto"
a = p.settings_api_args()
check(a["timeouts"]["udp"] == 900, f"the timeout must be sent: {a['timeouts']}")
check(a["timeouts"]["icmp"] == 0,
      "a timeout the operator did not set must be sent as zero, which is what "
      "the device means by inherit")
# Off and unset are different: the device cannot tell "the operator turned
# RTSP off" from "the operator said nothing" without the second field.
check(a["alg"] & config.Pool.ALG_BITS["rtsp"] == 0, "rtsp must be off")
check(a["alg_set"] & config.Pool.ALG_BITS["rtsp"] != 0,
      "and must be marked as chosen rather than defaulted")
check(a["alg"] & config.Pool.ALG_BITS["ftp"] != 0, "ftp stays on by default")
check(a["alg_set"] & config.Pool.ALG_BITS["ftp"] == 0,
      "and is not marked as chosen")
check(a["tcp_mss"] == 0 and a["tcp_mss_auto"] is True,
      f"an automatic clamp sends no number: {a['tcp_mss']}")

p.tcp_mss = "1400"
a = p.settings_api_args()
check(a["tcp_mss"] == 1400 and a["tcp_mss_auto"] is False,
      f"an explicit clamp sends the number: {a}")

b = p.bypass_api_args(is_dst=True)
check(b["is_destination"] is True and b["n_prefixes"] == len(b["prefixes"]),
      f"the count must match the list it describes: {b}")

f = p.forward_api_args(("udp", 5060, "100.64.0.9", 5060), is_add=False)
check(f["is_add"] is False and f["proto"] == "BPA_API_FWD_UDP",
      f"a forward removal must say so: {f}")

print("\n=== the MSS clamp is refused at the prompt, with the reason ===")
# Mirrors BPA_MSS_MIN/MAX in bpa_mss.h, which test_bpa_mss.c pins from the
# other side. Both have to move together or the VTY accepts what the dataplane
# refuses.
check(config.Pool.check_mss("1400") is None, "1400 is a legal clamp")
check(config.Pool.check_mss("auto") is None, "auto is legal")
check(config.Pool.check_mss("535") is not None, "535 is below the RFC floor")
check(config.Pool.check_mss("9001") is not None, "9001 is above a jumbo frame")
check(config.Pool.check_mss("wat") is not None, "a word is not a size")
check("RFC 1122" in (config.Pool.check_mss("14") or ""),
      "and the refusal must say which floor it is, not just 'invalid'")
check((config.Pool.MSS_MIN, config.Pool.MSS_MAX) == (536, 9000),
      "the bounds must match bpa_mss.h")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
