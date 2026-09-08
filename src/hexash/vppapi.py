#!/usr/bin/env python3
"""
Binary API client for the bpa plugin.

Why this exists: `vppctl` exits 0 even when the command it ran failed, so the
only evidence of a refusal is the text it printed. A control plane built on
that cannot tell "pool created" from "pool rejected", and `commit` reports
success while the dataplane holds none of the configuration.

Why vat2 and not vpp_papi: fd.io does not package the Python bindings. Using
them would mean vendoring vpp_papi out of the VPP source tree and keeping that
copy in step with every VPP bump. vat2 ships inside the `vpp` package itself,
so it is already on every installed box and every ISO, and it is versioned with
VPP by construction. The cost is a process per call rather than a persistent
connection, which is irrelevant for configuration - that happens at human
speed, and configuration is all hexash does.
"""

from __future__ import annotations

import json
import os
import subprocess

VAT2 = os.environ.get("VAT2", "vat2")
VPPCTL = os.environ.get("VPPCTL", "vppctl")

# Five outcomes, because they need different actions from the operator.
# Collapsing them into "it failed" is how an unplugged dataplane and a rejected
# configuration end up looking the same.
OK = "ok"
NO_CLIENT = "no-client"    # vat2 is not installed
NOT_RUNNING = "not-running"  # VPP is not answering
NO_PLUGIN = "no-plugin"    # VPP is up, the bpa API is not registered
REFUSED = "refused"        # the dataplane answered, and said no
BAD_REPLY = "bad-reply"    # it answered something this client cannot read

# From vnet/error.h. Only the codes this plugin can return are listed; anything
# else falls back to the number, which is still actionable when paired with the
# detail from VPP's log.
RETVAL_NAMES = {
    -1: "unspecified error",
    -2: "invalid interface",
    -6: "no such entry",
    -7: "invalid value",
    -9: "unimplemented",
    -30: "entry already exists",
    -102: "invalid argument",
}


# The messages that CHANGE something. Only these carry an identity: a dump
# that named an operator would put a line in nobody's trail and invite the
# reader to think reading was recorded too.
WRITE_MESSAGES = frozenset({
    "bpa_pool_add_del",
    "bpa_pool_settings_set",
    "bpa_bypass_set",
    "bpa_limits_set",
    "bpa_forward_add_del",
    "bpa_interface_add_del_feature",
})


def identity():
    """
    Who to record for a change made now: (name, uid).

    Read from the system at the moment of the call, never from anything this
    program stores - the same rule capabilities() follows, and for the same
    reason.

    SUDO_USER first, because it is the answer to the question being asked. An
    operator who runs this under sudo is root by the time the call is made,
    and a trail that records "root" for every one of them has recorded
    nothing: root is the account nobody is. The effective uid goes on the line
    as well rather than instead, so "alice uid 0" says what actually happened
    - alice, acting as root - which neither field says on its own.

    This is an assertion, not proof. sudo sets SUDO_USER and a person can set
    it themselves; the plugin cannot check it, because hexash reaches the
    dataplane over shared memory, which carries no peer credentials. Anyone
    who can make this call can already reconfigure the box, so the trail is
    the record of which operator did what among people trusted to be there -
    not evidence against them. That is issue #22, from the other side.
    """
    uid = os.geteuid()
    name = os.environ.get("SUDO_USER", "")
    if not name:
        try:
            import pwd
            name = pwd.getpwuid(uid).pw_name
        except Exception:
            # An account the system cannot describe is recorded as the number
            # it is, rather than guessed at.
            name = ""
    return name, uid


class Reply:
    """The result of one API call."""

    def __init__(self, state, retval=0, reason="", fields=None):
        self.state = state
        self.retval = retval
        self.reason = reason
        self.fields = fields or {}

    @property
    def ok(self):
        return self.state == OK

    def __repr__(self):
        return f"<Reply {self.state} retval={self.retval} {self.reason!r}>"


class Vat2:
    def __init__(self, vat2=None, vppctl=None, runner=None, identity=identity):
        self.vat2 = vat2 or VAT2
        self.vppctl = vppctl or VPPCTL
        # Injected so the outcomes can be tested without a running VPP. The
        # three failure paths are the ones that matter and the hardest to
        # produce on demand.
        self._run = runner or self._subprocess
        # Injected for the same reason the runner is: a test asserting what
        # goes on the wire must not depend on which account runs it.
        self._identity = identity

    @staticmethod
    def _subprocess(argv, timeout=10):
        try:
            p = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]}: timed out"

    # ------------------------------------------------------------- readiness

    def available(self):
        """
        (state, message). Checked in the order an operator would act on:
        is the tool here, is VPP up, does it know this plugin.
        """
        # Everything goes through the runner, including the "is it installed"
        # check. An earlier version used shutil.which here, which bypassed the
        # one seam this class has and made three of its four outcomes
        # untestable - the failure paths, which are the ones that matter.
        rc, apis, err = self._run([self.vat2, "--dump-apis"])
        if rc == 127:
            return NO_CLIENT, (
                f"{self.vat2} not found; it ships with the vpp package")
        if rc != 0:
            return NO_CLIENT, f"{self.vat2} failed: {(err or apis).strip()[:120]}"

        rc, out, err = self._run([self.vppctl, "show", "version"])
        if rc != 0 or not out.strip():
            return NOT_RUNNING, (
                "the dataplane is not answering; check 'systemctl status vpp'")

        if "bpa_pool_add_del" not in apis:
            return NO_PLUGIN, (
                "VPP is running but the CGNAT API is not registered; the vat2 "
                "plugin is missing from the install")
        return OK, "ready"

    # ------------------------------------------------------------- calling

    def call(self, message, args, timeout=15):
        if message in WRITE_MESSAGES:
            # Added here and not in the argument builders in config.py: those
            # are COMPARED to work out what changed, and an identity inside
            # them would make every commit by a different operator look like a
            # configuration change to every pool.
            name, uid = self._identity()
            args = dict(args, audit_user=name, audit_uid=uid)

        rc, out, err = self._run(
            [self.vat2, message, json.dumps(args)], timeout=timeout)

        if rc == 127:
            return Reply(NO_CLIENT, reason=err.strip() or "vat2 not found")

        text = (out or "") + (err or "")
        if "No such command" in text:
            return Reply(NO_PLUGIN, reason=(
                f"the dataplane does not know '{message}'; the vat2 plugin is "
                "missing from the install"))

        payload = self._parse(out)
        if payload is None:
            last = text.strip().splitlines()[-1] if text.strip() else ""
            # Two different failures share this branch and must not share a
            # diagnosis. Nothing on stdout means the call never reached VPP;
            # blaming the configuration for a dead socket sends the operator to
            # edit a pool that was never the problem. Output that is not JSON
            # means it answered something this client cannot read, which is a
            # version mismatch, not a configuration error.
            if out and out.strip():
                return Reply(BAD_REPLY, reason=(
                    f"unreadable reply from the dataplane: {last[:120]}"))
            return Reply(NOT_RUNNING,
                         reason=last or "no reply from the dataplane")

        retval = int(payload.get("retval", 0))
        if retval == 0:
            return Reply(OK, fields=payload)

        name = RETVAL_NAMES.get(retval, f"error {retval}")
        detail = self._last_log_error()
        reason = f"the dataplane refused it: {name}"
        if detail:
            reason += f" ({detail})"
        return Reply(REFUSED, retval=retval, reason=reason, fields=payload)

    def dump(self, message, args, timeout=15):
        """
        A dump answers with an ARRAY of details, not with one reply object.

        call() reads a single object from the first "{", which is right for a
        reply and wrong here: it would read the first entry and then fail on
        the text after it. Captured from a running dataplane rather than
        assumed - the shape is

            [{"_msgname": "sw_interface_details", "sw_if_index": 0, ...},
             {"_msgname": "sw_interface_details", "sw_if_index": 1, ...}]

        An empty dump is a legitimate answer and comes back as an empty list,
        which is not the same as a failure and must not be reported as one.
        """
        rc, out, err = self._run(
            [self.vat2, message, json.dumps(args)], timeout=timeout)

        if rc == 127:
            return Reply(NO_CLIENT, reason=err.strip() or "vat2 not found")

        text = (out or "") + (err or "")
        if "No such command" in text:
            return Reply(NO_PLUGIN, reason=(
                f"the dataplane does not know '{message}'"))

        start = (out or "").find("[")
        if start < 0:
            last = text.strip().splitlines()[-1] if text.strip() else ""
            return Reply(NOT_RUNNING,
                         reason=last or "no reply from the dataplane")
        try:
            rows = json.loads(out[start:])
        except ValueError:
            return Reply(BAD_REPLY,
                         reason="unreadable dump from the dataplane")
        return Reply(OK, fields={"rows": rows})

    def interface_index(self, name):
        """
        The sw_if_index for an interface name, or None.

        The dataplane's messages take an index and the operator types a name.
        That translation used to happen inside `vppctl set interface ...`,
        which is exactly the CLI access this is taking away, so it has to
        happen here instead.
        """
        r = self.dump("sw_interface_dump",
                      {"sw_if_index": 0xFFFFFFFF,
                       "name_filter_valid": False, "name_filter": ""})
        if r.state != OK:
            return None
        for row in r.fields.get("rows", []):
            if row.get("interface_name") == name:
                return row.get("sw_if_index")
        return None

    @staticmethod
    def _parse(out):
        """vat2 prints a JSON object; anything before it is noise."""
        if not out:
            return None
        start = out.find("{")
        if start < 0:
            return None
        try:
            return json.loads(out[start:])
        except ValueError:
            return None

    def _last_log_error(self):
        """
        The plugin sends the detailed reason to VPP's log and returns only a
        code, so the number alone is not actionable. This recovers the sentence
        an operator can act on.
        """
        rc, out, _ = self._run([self.vppctl, "show", "log"])
        if rc != 0 or not out:
            return ""
        for line in reversed(out.splitlines()):
            if "bpa" in line and ("err" in line.lower() or "error" in line.lower()):
                return line.split("bpa", 1)[-1].lstrip(" :/").strip()[:160]
        return ""
