#!/usr/bin/env python3
"""
What commit does when the dataplane refuses at apply time.

The lab proves validation catches a bad pool. It cannot prove this: validation
runs first, so a pool that fails it never reaches the apply path. The case that
matters here is the one validation does not catch - the dataplane accepting a
check and refusing the write - because that is precisely where the old code
reported success while holding none of the configuration.

The API is stubbed, so each outcome is produced on demand rather than raced
for.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "hexash"))
import config      # noqa: E402
import hexash      # noqa: E402
import vppapi      # noqa: E402

fail = 0


def check(cond, msg):
    global fail
    if not cond:
        fail += 1
        print(f"  FAIL: {msg}")


class FakeApi:
    """Answers OK for everything except the pools named in `refuse`."""

    def __init__(self, refuse=(), state=vppapi.OK):
        self.refuse = set(refuse)
        self.state = state
        self.calls = []

    def available(self):
        return (self.state, "stub")

    def call(self, message, args):
        self.calls.append((message, args))
        if args.get("name") in self.refuse and args.get("is_add"):
            return vppapi.Reply(vppapi.REFUSED, retval=-7,
                                reason="the dataplane refused it: invalid value")
        return vppapi.Reply(vppapi.OK)


class FakeVpp:
    """A dataplane that accepts every CLI command, including validation."""

    TRACE = False

    def __init__(self):
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        return 0, ""

    def running(self):
        return 0, ""


def shell_with(api, running_text=""):
    sh = hexash.Shell()
    sh.api = api
    sh.dp = FakeVpp()
    sh.frr = type("F", (), {"available": lambda self: False,
                            "reload": lambda self, l, check_only=False: (0, "")})()
    sh._running_text = running_text
    sh.running_config = lambda: config.Config.parse(running_text)
    return sh


def target_with(name, inside, outside, block="512"):
    cfg = config.Config()
    p = config.Pool(name)
    p.inside = [inside]
    p.outside = [outside]
    p.block_size = block
    cfg.pools[name] = p
    return cfg


print("=== the dataplane refuses at apply: commit must fail and name the pool ===")
api = FakeApi(refuse={"doomed"})
sh = shell_with(api)
ok = sh.apply(target_with("doomed", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is False, "apply must report failure when the dataplane refuses")
check(sh.failed > 0, "the failure must be counted, not just returned")
print(f"  apply -> {ok}, errors recorded: {sh.failed}")

print("\n=== a refusal must not be reported as success ===")
# The defect being removed: vppctl exits 0 on failure, so the old path returned
# True here and the operator was told the pool existed.
api = FakeApi(refuse={"doomed"})
sh = shell_with(api)
sh.apply(target_with("doomed", "100.64.0.0/22", "200.150.0.16/30"))
sent = [a["name"] for m, a in api.calls if a.get("is_add")]
check("doomed" in sent, f"the pool must have been attempted: {sent}")
print(f"  attempted: {sent}")

print("\n=== an accepted pool goes through ===")
api = FakeApi()
sh = shell_with(api)
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is True, "an accepted pool must succeed")
check(any(a.get("name") == "fine" and a.get("is_add") for m, a in api.calls),
      "the pool must reach the API")
print(f"  apply -> {ok}")

print("\n=== an unreachable dataplane fails before anything is applied ===")
# Finding out halfway through leaves the box in neither the old configuration
# nor the new one, with no way to tell which pools made it.
api = FakeApi(state=vppapi.NOT_RUNNING)
sh = shell_with(api)
ok = sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"))
check(ok is False, "an unreachable dataplane must fail the commit")
check(api.calls == [], f"nothing may be sent before the check: {api.calls}")
print(f"  apply -> {ok}, calls made: {len(api.calls)}")

print("\n=== commit check sends no write, whatever the outcome ===")
# A check that mutates is not a check. An earlier version deleted and recreated
# the running pool to work around a name check, turning a read-only operation
# into an outage risk.
api = FakeApi()
sh = shell_with(api)
sh.apply(target_with("fine", "100.64.0.0/22", "200.150.0.16/30"), check_only=True)
writes = [m for m, a in api.calls if m == "bpa_pool_add_del"]
check(writes == [], f"commit check must write nothing, sent: {writes}")
print(f"  writes during check: {len(writes)}")

print(f"\n{'ALL TESTS PASSED' if not fail else str(fail) + ' FAILURES'}")
sys.exit(1 if fail else 0)
