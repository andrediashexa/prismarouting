#!/usr/bin/env python3
"""
Binary API client for the bpa plugin.

Why this exists: `vppctl` exits 0 even when the command it ran failed, so the
only evidence of a refusal is the text it printed. A control plane built on
that cannot tell "pool created" from "pool rejected", and `commit` reports
success while the dataplane holds none of the configuration.

Two transports behind one interface (#527). `Papi` holds ONE connection to
the dataplane's API socket for the whole session, through vpp_papi - fd.io
packages it as python3-vpp-api, at the same version as the `vpp` it talks to,
so it is pinned with VPP like vat2 is. `Vat2` starts a vat2 process per
message, and is what `Papi` falls back to when the library, the API
definitions or the socket are not there.

WHY NOT vat2 ALONE ANY MORE. This file used to say a process per call is
irrelevant, because configuration happens at human speed. Measured, it is not:
on the X520 bench (a VM, #475) a vat2 costs ~130 ms whatever the message, a
commit of one interface description sent 50 of them and took 9.7 s, 6.5 s of
it starting processes. Over one connection the same 50 messages take ~0.1 s
(lab: 2.64 s against 0.117 s, after a 0.4-0.5 s connect).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess

VAT2 = os.environ.get("VAT2", "vat2")
VPPCTL = os.environ.get("VPPCTL", "vppctl")

# Six outcomes, because they need different actions from the operator.
# Collapsing them into "it failed" is how an unplugged dataplane and a rejected
# configuration end up looking the same.
OK = "ok"
NO_CLIENT = "no-client"    # vat2 is not installed
NOT_RUNNING = "not-running"  # VPP is not answering
NO_PLUGIN = "no-plugin"    # VPP is up, the bpa API is not registered
REFUSED = "refused"        # the dataplane answered, and said no
BAD_REPLY = "bad-reply"    # it answered something this client cannot read
# CANNOT_SEND is the client's own failure and needs the opposite action from
# NOT_RUNNING, which is where it used to land: nothing reached VPP, VPP is
# fine, and the operator has to change what they asked for - not restart the
# dataplane. vat2 makes that distinction hard to see, because it CRASHES on a
# value it cannot encode (empty output, killed by a signal) and exits 0 with
# "Failed converting JSON to API" on stderr when it merely refuses. Both used
# to come back as "the dataplane is not answering; check systemctl" (#136).
CANNOT_SEND = "cannot-send"  # the client could not say it; VPP never saw it

# Fields VPP declares as a plain `enum` although every member is a bit.
#
# vat2 generates, for an `enum`, a parser that compares ONE name whole:
#
#     char *p = cJSON_GetStringValue(o);
#     if (strcmp(p, "SUB_IF_API_FLAG_TWO_TAGS") == 0) {*a = 4; return 0;}
#
# (vnet/interface_types.api_fromjson.h). cJSON_GetStringValue returns NULL for
# anything that is not a JSON string, and strcmp reads it. Measured against
# vat2 from vpp 26.06-release:
#
#     "SUB_IF_API_FLAG_TWO_TAGS"                       reply, retval 0
#     "SUB_IF_API_FLAG_TWO_TAGS|SUB_IF_API_FLAG_EXACT_MATCH"  exit 0, refused
#     "SUB_IF_API_FLAG_TWO_TAGS,SUB_IF_API_FLAG_EXACT_MATCH"  exit 0, refused
#     20                                               SIGSEGV
#     0                                                SIGSEGV
#     null                                             SIGSEGV
#     ["SUB_IF_API_FLAG_TWO_TAGS"]                     SIGSEGV
#     ["SUB_IF_API_FLAG_TWO_TAGS","SUB_IF_API_FLAG_EXACT_MATCH"]  SIGSEGV
#
# A type VPP declares as `enumflags` instead - tunnel_flags, af_xdp_flag,
# ip_flow_hash_config - generates a loop over a JSON ARRAY that ORs the
# members, and takes [] for none. So the multi-bit value is expressible in
# vat2's JSON, and these two types are simply declared with the wrong keyword
# upstream: it is the API definition, not the client's design.
#
# The check below is on the SHAPE of the value and not on a list of member
# names. A list of names is one more thing to keep in step with VPP, and
# getting it wrong would refuse a flag that works - a worse failure than the
# one it prevents.
ENUM_FLAG_FIELDS = {
    "create_subif": ("sub_if_flags",),
    "sw_interface_set_flags": ("flags",),
}


def unsendable(message, args):
    """
    Why vat2 cannot encode `args`, or "" when it can.

    Checked before the process is started, so prismash never hands vat2 the
    value it dies on. A core dump in the middle of a commit is not a
    diagnosis, and the operator would be reading it instead of the sentence
    that says which field could not be said.
    """
    for field in ENUM_FLAG_FIELDS.get(message, ()):
        if field not in args:
            continue
        value = args[field]
        if not isinstance(value, str):
            return (f"{field} is sent as one flag NAME; vat2 reads a null "
                    f"pointer for anything else, and {value!r} crashes it")
        if not value or any(c in value for c in "|, "):
            return (f"{field} takes exactly ONE flag name and {value!r} is "
                    "not one; vat2 cannot send a combination of them")
    return ""


# From vnet/error.h. Only the codes this plugin can return are listed; anything
# else falls back to the number, which is still actionable when paired with the
# detail from VPP's log.
RETVAL_NAMES = {
    -147: "the change is refused while it is in use",
    -1: "unspecified error",
    -2: "invalid interface",
    -6: "no such entry",
    -7: "invalid value",
    -9: "unimplemented",
    -30: "entry already exists",
    -102: "invalid argument",
}


def is_question(message):
    """
    Does `message` only ask? VPP's convention names the ones that answer
    without changing anything *_get (sw_interface_get_table,
    lcp_osi_proto_get); everything else sent through call() may change
    what the dataplane says (#520).
    """
    return message.endswith(("_get", "_get_table"))


# The messages that CHANGE something. Only these carry an identity: a dump
# that named an operator would put a line in nobody's trail and invite the
# reader to think reading was recorded too.
WRITE_MESSAGES = frozenset({
    "bpa_pool_add_del",
    "bpa_pool_settings_set",
    "bpa_bypass_set",
    "bpa_limits_set",
    "bpa_forward_add_del",
    # Attaching or detaching CGNAT on an interface is a change with
    # operational consequence - detaching stops translation for everyone
    # behind it - so it names who did it like the rest (#124).
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
    it themselves; the plugin cannot check it, because prismash reaches the
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

    # not a unit: a property is read and never called, so no test can name
    # it for the reader; test_vppapi.py reads it both ways all the same
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
        # The dumps answered inside cached_dumps(), or None outside it (#520).
        self._dumps = None

    #: VPP's shared-memory API segment, and the group an operator account
    #: reaches it through.
    API_SEGMENT = os.environ.get("PRISMA_API_SEGMENT", "/dev/shm/global_vm")
    API_GROUP = "vpp"

    @classmethod
    def _keep_segment_reachable(cls):
        """
        Put the API segment's group back after a root client has attached.

        Measured: one client running as root resets /dev/shm/global_vm to
        root:root, and every non-root client afterwards dies inside
        svm_region_map - vat2 with SIGSEGV, which reads like a client bug
        rather than a permission one.

        That matters on a shipped box because `prisma-routing.service` applies the
        saved configuration AS ROOT at every boot, so the drop-in that sets the
        group when VPP starts is undone by the very next unit. The release gate
        caught it: an installed box reported the binary API root-only, and the
        boot self-test that found it is the one that stopped SYNTHESISING the
        group to prove reach (#184, #185).

        Repaired by the thing that breaks it, rather than by another unit
        chasing it, because an interactive root session breaks it too and no
        unit runs after that one.

        Best effort and silent: a box where the group does not exist, or where
        this is not root, is not a box this can fix, and failing a commit over
        it would refuse a change that worked.
        """
        if os.geteuid() != 0:
            return
        try:
            import grp
            gid = grp.getgrnam(cls.API_GROUP).gr_gid
            st = os.stat(cls.API_SEGMENT)
            if st.st_gid != gid or (st.st_mode & 0o777) != 0o660:
                os.chown(cls.API_SEGMENT, 0, gid)
                os.chmod(cls.API_SEGMENT, 0o660)
        except (KeyError, OSError, ImportError):
            pass

    @classmethod
    def segment_denied(cls):
        """
        Why this account cannot reach the dataplane's API segment, or None.

        An account without the group (#561): vat2 does not fail when it cannot
        map /dev/shm/global_vm, it waits out its connect, and every dump a
        running-config read makes waited its 10-15 s in turn - 90.6 s for one
        `show running-config` in lab/mtu-and-readonly.sh, against 0.5 s for
        root, for the same 14 lines. Asked of the segment itself rather than of
        the account's groups: the segment's mode is what vat2 meets, and a box
        that changed it is answered by what it is, not by what it should be. A
        segment that is not there (the dataplane down) is left to vat2, which
        says so itself.
        """
        try:
            if os.path.exists(cls.API_SEGMENT) and \
                    not os.access(cls.API_SEGMENT, os.R_OK | os.W_OK):
                return (f"this account cannot reach the dataplane's API: "
                        f"{cls.API_SEGMENT} is for the {cls.API_GROUP} group")
        except OSError:
            return None
        return None

    @classmethod
    def _subprocess(cls, argv, timeout=10):
        # The one place every vat2 goes through, so an account that cannot
        # reach the segment is answered at once instead of after a timeout
        # per dump (#561). vppctl is not affected: its socket refuses at once.
        if os.path.basename(argv[0]).startswith("vat2") and \
                argv[1:2] != ["--dump-apis"]:
            why = cls.segment_denied()
            if why:
                return 126, "", why
        try:
            p = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"{argv[0]}: timed out"
        finally:
            cls._keep_segment_reachable()

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

    @contextlib.contextmanager
    def cached_dumps(self):
        """
        Answer a dump asked again with what it answered the first time, for
        as long as the block runs - and forget everything at any call().

        WHY (#520): every vat2 is a process, 100-150 ms of start-up on the
        X520 bench whatever the message, and one `show running-config` asked
        for the same sw_interface_dump twenty times over, once per
        interface_index(). A commit check with nothing to change took 15.5 s,
        12.7 s of it in 96 vat2 processes.

        FOR READS ONLY. The caller opens it where nothing is written - reading
        the running configuration, checking a candidate - and call() clears
        it anyway, so a write inside the block is never followed by the
        answer from before it. Nested blocks share the outermost's answers.
        """
        outer = self._dumps is None
        if outer:
            self._dumps = {}
        try:
            yield self
        finally:
            if outer:
                self._dumps = None


    def recall(self, key):
        """
        What remember() kept under `key` in this block, or None: outside a
        block, after any write, and for a key never kept (#529).
        """
        if self._dumps is None:
            return None
        return self._dumps.get(("kept", key))

    def remember(self, key, value):
        """
        Keep `value` under `key` until the block ends or anything is written
        (#529) - what a caller built out of this block's reads, such as the
        whole running configuration, so a second reading is not made of them.
        Outside a block it keeps nothing.
        """
        if self._dumps is not None:
            self._dumps[("kept", key)] = value

    def forget_dumps(self):
        """
        Stop answering from the block's cache, now, until another block opens.

        For the one place a block has to end before its `with` does (#527): a
        real apply reads the running configuration and then WRITES - over this
        API, which clears the cache itself, but also through vppctl and the
        host's doors, which cannot, and linux_cp carries a Linux change back
        into what a dump says. So the apply drops the answers the moment it
        has read, and everything after it asks the dataplane.
        """
        self._dumps = None

    @staticmethod
    def _client_failure(message, rc, text):
        """
        The Reply for a vat2 that never got the message out, or None.

        Split out because a dump has to reach the same conclusion as a call: a
        client that crashed while encoding sw_interface_dump used to surface
        as interface_index() returning None, and from there as "the dataplane
        does not have it" about an interface that is right there.
        """
        # subprocess reports a signal as a negative code; a shell in between
        # reports it as 128+n. Both mean the same thing here.
        if rc < 0 or rc >= 128:
            sig = -rc if rc < 0 else rc - 128
            return Reply(CANNOT_SEND, reason=(
                f"the client crashed (signal {sig}) encoding '{message}'; "
                "the dataplane never saw it"))
        if rc == 126 and text.startswith("this account cannot reach"):
            # segment_denied(), answered before any client ran (#561).
            return Reply(CANNOT_SEND, reason=text.strip())
        if "Failed converting JSON to API" in text:
            return Reply(CANNOT_SEND, reason=(
                f"the client cannot express '{message}' as it was asked to; "
                "the dataplane never saw it"))
        return None

    def call(self, message, args, timeout=15):
        """
        One message and its reply. Inside cached_dumps() a question - VPP
        names them *_get - is answered once like a dump, and anything else
        forgets every answer, since it may change them (#520).
        """
        if self._dumps is not None:
            if is_question(message):
                key = ("call", message, json.dumps(args, sort_keys=True))
                if key not in self._dumps:
                    r = self._call(message, args, timeout)
                    if r.state != OK:
                        return r
                    self._dumps[key] = r
                return self._dumps[key]
            self._dumps.clear()
        return self._call(message, args, timeout)

    def _call(self, message, args, timeout=15):
        """call() without the cache: one vat2, its reply or why not."""
        why = unsendable(message, args)
        if why:
            return Reply(CANNOT_SEND, reason=(
                f"'{message}' cannot be sent over the binary API: {why}"))

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
        mine = self._client_failure(message, rc, text)
        if mine:
            return mine

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
        key = (message, json.dumps(args, sort_keys=True))
        if self._dumps is not None and key in self._dumps:
            return self._dumps[key]
        r = self._dump(message, args, timeout)
        # Only an answer is kept: a failure asked again is asked again.
        if self._dumps is not None and r.state == OK:
            self._dumps[key] = r
        return r

    def _dump(self, message, args, timeout):
        """dump() without the cache: one vat2, its rows or why not."""
        rc, out, err = self._run(
            [self.vat2, message, json.dumps(args)], timeout=timeout)

        if rc == 127:
            return Reply(NO_CLIENT, reason=err.strip() or "vat2 not found")

        text = (out or "") + (err or "")
        if "No such command" in text:
            return Reply(NO_PLUGIN, reason=(
                f"the dataplane does not know '{message}'"))
        mine = self._client_failure(message, rc, text)
        if mine:
            return mine

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


# --- one connection: vpp_papi (#527) ---------------------------------------

#: The binary API's unix socket: `socksvr { default }` in the box's
#: startup.conf, and /run/vpp is mounted into the management container.
API_SOCKET = os.environ.get("PRISMA_API_SOCKET", "/run/vpp/api.sock")
#: Where the API definitions are: VPP's own (the `vpp` package) and this
#: tree's plugin's bpa.api.json, which install.sh and the dataplane image put
#: beside them.
API_JSON_DIR = os.environ.get("PRISMA_API_JSON", "/usr/share/vpp/api")
#: `PRISMA_API=vat2` keeps every message on vat2: the way back if the
#: library misbehaves on a box, and how a lab measures the two against each
#: other.
FORCE_VAT2 = os.environ.get("PRISMA_API", "") == "vat2"
#: The plugins whose messages prismash sends, beside VPP's core definitions.
#: Loading these and not all 80-odd plugin files halves the connect (lab:
#: 0.48 s for 144 files, 0.24 s for 59). A message outside them is still sent
#: - over vat2, one process, by _defined() - and lab/api.sh reads prismash.py
#: for every message it sends and fails if one would take that way.
API_PLUGINS = ("bpa", "lcp", "urpf", "vrrp")


class _PapiTeardown(logging.Filter):
    """
    Drops the one record vpp_papi's socket transport logs while it is being
    disconnected: its reader thread selects on a socket disconnect() has just
    closed, logs "select failed" at ERROR on the root logger and exits.
    Measured on the X520 bench (0.148.1) as `ERROR:root:select failed` on the
    stderr of about one `prismash -c` in five, after a command that had
    succeeded. Anything else vpp_papi logs is left alone.
    """

    def filter(self, record):
        return record.getMessage() != "select failed"


def quiet_papi_teardown():
    """Put _PapiTeardown on the root logger, once."""
    root = logging.getLogger()
    if not any(isinstance(f, _PapiTeardown) for f in root.filters):
        root.addFilter(_PapiTeardown())


def plain(value, top=True):
    """
    A vpp_papi reply as the JSON vat2 prints for it, which is what every
    caller of call() and dump() reads.

    vpp_papi hands back typed values - a namedtuple per message and per
    struct, IntEnum and IntFlag members, MACAddress, ipaddress objects - and
    vat2 printed names and strings. Converted here, once, so no caller learns
    there are two transports: a message's fields become a dict (without the
    papi-only `_0` and `context`), an enum its member's name, an address or a
    MAC its text. A flag field comes back as its members joined with "|";
    vat2 printed "Invalid ENUM" for most of them, and nothing reads one.
    `_msgname` is the MESSAGE's, as vat2 prints it: a struct inside one has
    none (`top`, measured by lab/api.sh's parity run).
    """
    import enum
    if isinstance(value, tuple) and hasattr(value, "_asdict"):
        out = {"_msgname": type(value).__name__} if top else {}
        for k, v in value._asdict().items():
            if k not in ("_0", "context"):
                out[k] = plain(v, False)
        return out
    if isinstance(value, enum.Enum):
        return value.name if value.name else int(value.value)
    if isinstance(value, (list, tuple)):
        return [plain(v, top) for v in value]
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, str):
        # A C STRING ENDS AT ITS FIRST NUL, and vat2 printed it that way:
        # vpp_papi can hand a fixed-size string field back with its padding.
        # Found looking for #530's NUL, which came through cli() - a field
        # that kept its padding would have been the same defect by this path.
        return value.split("\x00", 1)[0]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def encode(message, args, fieldtypes, types):
    """
    `args`, written for vat2, as vpp_papi packs them.

    The one difference that matters: vat2 takes an enum by its member's NAME
    ("URPF_API_MODE_STRICT") and vpp_papi packs only the number, refusing the
    name with a serializer error. Every call site in prismash writes names, so
    they are turned into numbers here, by the type the message declares for
    the field - a struct field is followed into its own declaration. Strings
    for addresses, prefixes and MACs pack as they are.

    `fieldtypes` is {field: type name} for the message; `types` is
    vpp_papi's registry of type objects. A name that is not a member raises
    KeyError, which the caller reports as a value the client cannot send.
    """
    # not a unit: a closure inside encode, driven through encode() by
    # test/test_vppapi.py on enums, flag lists, structs and arrays.
    def conv(tname, value):
        t = types.get(tname)
        if t is None:
            return value
        members = getattr(t, "enum", None)
        if members is not None:
            if isinstance(value, str):
                return int(members[value])
            if isinstance(value, list):
                out = 0
                for name in value:
                    out |= int(members[name]) if isinstance(name, str) else int(name)
                return out
            return value
        sub = getattr(t, "msgdef", None)
        if isinstance(value, dict) and sub:
            inner = {f[1]: f[0] for f in sub if isinstance(f, list) and len(f) >= 2}
            return {k: conv(inner.get(k, ""), v) for k, v in value.items()}
        if isinstance(value, list):
            return [conv(tname, v) for v in value]
        return value

    return {k: conv(fieldtypes.get(k, ""), v) for k, v in args.items()}


class Papi(Vat2):
    """
    The dataplane over one vpp_papi connection per process, and vat2 when that
    cannot be had.

    Connected on the first message, not at start: a `show` that never asks
    the dataplane anything pays nothing. A connection that dies (VPP
    restarted under a long session) is re-opened once. A QUESTION is then
    asked again; a WRITE is not re-sent, because nothing here can tell
    whether the first copy reached VPP before the socket went, and a write
    applied twice is worse than a commit that says it failed.

    `transport` says which is in use, and `why_vat2` why it is not vpp_papi.
    """

    def __init__(self, vat2=None, vppctl=None, runner=None, identity=identity,
                 papi=None, socket=None, apidir=None):
        super().__init__(vat2=vat2, vppctl=vppctl, runner=runner,
                         identity=identity)
        self._papi = papi
        self._socket = socket or API_SOCKET
        self._apidir = apidir or API_JSON_DIR
        self._conn = None
        self.why_vat2 = "PRISMA_API=vat2" if FORCE_VAT2 else ""

    # not a unit: a property is read and never called; test/test_vppapi.py
    # reads it on a connection, a missing library and PRISMA_API=vat2.
    @property
    def transport(self):
        """'vpp_papi' while the connection is (or can be) used, else 'vat2'."""
        return "vat2" if self.why_vat2 else "vpp_papi"

    def _module(self):
        """vpp_papi, or None with why_vat2 saying why not."""
        if self._papi is None:
            try:
                import vpp_papi
                self._papi = vpp_papi
            except ImportError:
                self.why_vat2 = "python3-vpp-api is not installed"
                return None
        return self._papi

    def _connect(self):
        """The open connection, opening it if need be; None means vat2."""
        if self._conn is not None:
            return self._conn
        if self.why_vat2:
            return None
        papi = self._module()
        if papi is None:
            return None
        import glob
        files = glob.glob(os.path.join(self._apidir, "core", "*.api.json"))
        files += [f for f in (os.path.join(self._apidir, "plugins",
                                           f"{p}.api.json")
                              for p in API_PLUGINS) if os.path.exists(f)]
        if not files:
            self.why_vat2 = f"no API definitions under {self._apidir}"
            return None
        # Only what the VTY sends (#529), made once and kept; the files as
        # they are when that cannot be had.
        pruned = definitions_file(files)
        quiet_papi_teardown()
        try:
            conn = papi.VPPApiClient(apifiles=[pruned] if pruned else files,
                                     use_socket=True,
                                     server_address=self._socket,
                                     read_timeout=15)
            conn.connect(f"prismash-{os.getpid()}")
        except Exception as e:  # noqa: BLE001 - every failure means vat2
            self.why_vat2 = f"{self._socket}: {str(e)[:80] or type(e).__name__}"
            return None
        self._conn = conn
        return conn

    def close(self):
        """Disconnect; the next message connects again."""
        if self._conn is not None:
            try:
                self._conn.disconnect()
            except Exception:  # noqa: BLE001 - it is going away either way
                pass
            self._conn = None

    def _send(self, message, args, question):
        """
        (Reply or None, answer): None and vpp_papi's answer when it was
        asked, or a Reply saying why not. Re-connects once on a dead socket,
        and asks again only a `question`.
        """
        for attempt in (0, 1):
            conn = self._connect()
            if conn is None:
                return Reply(NOT_RUNNING, reason=self.why_vat2), None
            fn = getattr(conn.api, message, None)
            if fn is None:
                # Defined, and VPP did not list it at connect: the plugin that
                # answers it is not loaded.
                return Reply(NO_PLUGIN, reason=(
                    f"the dataplane does not know '{message}'")), None
            try:
                msg = conn.messages[message]
                fields = dict(zip(msg.fields, msg.fieldtypes))
                packed = encode(message, args, fields,
                                self._papi.vpp_serializer.types)
            except Exception as e:  # noqa: BLE001 - a value it cannot say
                return Reply(CANNOT_SEND, reason=(
                    f"'{message}' cannot be sent over the binary API: "
                    f"{str(e)[:120]}")), None
            try:
                return None, fn(**packed)
            except (OSError, EOFError) as e:
                self._conn = None
                if attempt or not question:
                    return Reply(NOT_RUNNING, reason=(
                        f"the connection to the dataplane was lost: "
                        f"{str(e)[:100] or type(e).__name__}")), None
            except Exception as e:  # noqa: BLE001
                name = type(e).__name__
                if "Serializer" in name or isinstance(e, (TypeError,
                                                          ValueError)):
                    return Reply(CANNOT_SEND, reason=(
                        f"'{message}' cannot be sent over the binary API: "
                        f"{str(e)[:120]}")), None
                self._conn = None
                if attempt or not question:
                    return Reply(NOT_RUNNING, reason=(
                        f"no reply from the dataplane: {str(e)[:100] or name}")), None
        return Reply(NOT_RUNNING, reason="no reply from the dataplane"), None

    def _defined(self, message):
        """
        Is `message` in the API definitions this connection loaded? When it
        is not - bpa.api.json missing beside VPP's own, as on a box upgraded
        from before #527 - vat2 still sends it: vpp_papi having no method for
        a message it was never told about says nothing about the dataplane.
        """
        conn = self._connect()
        return conn is not None and message in conn.messages

    def available(self):
        """available(), over the connection when there is one."""
        conn = self._connect()
        if conn is None or not self._defined("bpa_pool_add_del"):
            return super().available()
        if getattr(conn.api, "bpa_pool_add_del", None) is None:
            return NO_PLUGIN, (
                "VPP is running but the CGNAT API is not registered; the "
                "bpa plugin is not loaded, or bpa.api.json is missing")
        return OK, "ready"

    def _call(self, message, args, timeout=15):
        """_call() over the connection, or vat2's when there is none."""
        if not self._defined(message):
            return super()._call(message, args, timeout)
        why = unsendable(message, args)
        if why:
            return Reply(CANNOT_SEND, reason=(
                f"'{message}' cannot be sent over the binary API: {why}"))
        if message in WRITE_MESSAGES:
            name, uid = self._identity()
            args = dict(args, audit_user=name, audit_uid=uid)
        failed, answer = self._send(message, args, is_question(message))
        if failed:
            return failed
        payload = plain(answer)
        retval = int(payload.get("retval", 0) or 0)
        if retval == 0:
            return Reply(OK, fields=payload)
        name = RETVAL_NAMES.get(retval, f"error {retval}")
        detail = self._last_log_error()
        reason = f"the dataplane refused it: {name}"
        if detail:
            reason += f" ({detail})"
        return Reply(REFUSED, retval=retval, reason=reason, fields=payload)

    def cli(self, cmd):
        """
        (reached, text) for one CLI line over the connection, as cli_inband -
        or None when there is no connection, and the caller runs vppctl as it
        always did (#529).

        The text is vppctl's, byte for byte: lab/api.sh compares every screen
        the VTY reads through both on one dataplane. A `show` is answered once
        inside cached_dumps(); any other line may change what every answer
        says, and forgets them. A line that was SENT and lost with the socket
        is not handed to vppctl to send again - it is said as not reached.
        """
        question = cli_is_question(cmd)
        key = ("cli", cmd)
        if question and self._dumps is not None and key in self._dumps:
            return self._dumps[key]
        if not question and self._dumps is not None:
            self._dumps.clear()
        if not self._defined("cli_inband"):
            return None
        failed, answer = self._send("cli_inband", {"cmd": cmd}, question)
        if failed:
            # A question lost with the socket is asked again the old way,
            # which can only read; a write is not.
            if question or failed.state == CANNOT_SEND:
                return None
            return False, failed.reason
        # NO NUL IN A SCREEN, as vppctl prints none: VPP formats some names
        # with their C terminator inside the line - `show lcp`'s itf-pair rows
        # carried "enp6s18\x00" on the X520 bench (0.148.1), and the VTY read
        # every card's Linux name as differing from its own. vppctl's output
        # of the same screen has no such byte.
        got = (True, (answer.reply or "").replace("\x00", ""))
        if question and self._dumps is not None:
            self._dumps[key] = got
        return got

    def _dump(self, message, args, timeout=15):
        """_dump() over the connection, or vat2's when there is none."""
        if not self._defined(message):
            return super()._dump(message, args, timeout)
        failed, answer = self._send(message, args, True)
        if failed:
            return failed
        return Reply(OK, fields={"rows": [plain(r) for r in answer or []]})


#: Every message the VTY sends, and the one it reads its screens with (#529).
#: The connection loads the definitions of these alone - with their replies,
#: the types they reach and memclnt whole - rather than 58 files of them:
#: vpp_papi builds a serializer for every type and message it is handed, and
#: on the X520 bench that was 0.64 s of every new process. A message missing
#: here is still sent, over vat2 (_defined answers no), which is slow and
#: right; test/test_vppapi.py holds this list to prismash's source.
VTY_MESSAGES = frozenset({
    "bond_add_member", "bond_create2", "bond_delete", "bond_detach_member",
    "bpa_bypass_set", "bpa_forward_add_del", "bpa_global_set",
    "bpa_identity_radius_set", "bpa_identity_source_set",
    "bpa_interface_add_del_feature", "bpa_limits_set", "bpa_logging_set",
    "bpa_logging_syslog_set", "bpa_pool_add_del", "bpa_pool_settings_set",
    "cli_inband", "create_loopback_instance", "create_subif",
    "create_vlan_subif", "delete_loopback", "delete_subif", "ip_table_add_del",
    "ip_table_dump", "ipfix_exporter_dump", "lcp_itf_pair_add_del",
    "lcp_osi_proto_enable", "lcp_osi_proto_get", "set_ipfix_exporter",
    "sw_bond_interface_dump", "sw_interface_add_del_address",
    "sw_interface_dump", "sw_interface_get_table",
    "sw_interface_ip6nd_ra_config", "sw_interface_ip6nd_ra_dump",
    "sw_interface_set_flags", "sw_interface_set_interface_name",
    "sw_interface_set_mac_address", "sw_interface_set_mtu",
    "sw_interface_set_table", "sw_interface_tag_add_del",
    "sw_interface_clear_stats", "sw_member_interface_dump", "urpf_update",
    "vrrp_vr_add_del",
    "vrrp_vr_dump", "vrrp_vr_start_stop", "vrrp_vr_track_if_add_del",
    "vrrp_vr_track_if_dump",
})

#: The definition sections vpp_papi reads, and how a type is named in each.
_TYPE_SECTIONS = ("enums", "enumflags", "unions", "types")


def _bare(name):
    """A type as the definitions key it: `vl_api_address_t` is `address`."""
    if name.startswith("vl_api_") and name.endswith("_t"):
        return name[len("vl_api_"):-len("_t")]
    return name


def prune_definitions(apis, keep):
    """
    One definition file holding what `keep` needs out of `apis` (#529).

    `apis` is the parsed .api.json files, `keep` the request messages. Kept:
    those messages, the replies and details their services name, every
    message of memclnt (the connection itself: sockclnt_create, control_ping
    for the end of a dump, keepalives), the address family enum vpp_papi
    checks at start, and every type any of them reaches, followed through
    types, unions and aliases. A request not in any file is left out, and
    sent over vat2 by whoever asks for it.
    """
    msgs, services, tables = {}, {}, {k: {} for k in _TYPE_SECTIONS}
    aliases, memclnt = {}, set()
    for api in apis:
        mod = api.get("module", "") or ""
        for m in api.get("messages", []):
            msgs[m[0]] = m
            if mod == "memclnt" or "memclnt" in str(api.get("paths", "")):
                memclnt.add(m[0])
        services.update(api.get("services", {}))
        for sec in _TYPE_SECTIONS:
            for t in api.get(sec, []):
                tables[sec][t[0]] = t
        aliases.update(api.get("aliases", {}))
    want = set(memclnt) | {"control_ping", "control_ping_reply"}
    for k in keep:
        if k in msgs:
            want.add(k)
            reply = services.get(k, {}).get("reply")
            if reply:
                want.add(reply)
            for ev in services.get(k, {}).get("events", []) or []:
                want.add(ev)
    want &= set(msgs)
    types = {sec: {} for sec in _TYPE_SECTIONS}
    keep_alias = {}
    todo = ["address_family"]
    for m in want:
        todo += [f[0] for f in msgs[m][1:] if isinstance(f, list) and f]
    seen = set()
    while todo:
        name = _bare(todo.pop())
        if name in seen:
            continue
        seen.add(name)
        for sec in _TYPE_SECTIONS:
            t = tables[sec].get(name)
            if t is not None:
                types[sec][name] = t
                if sec in ("types", "unions"):
                    todo += [f[0] for f in t[1:] if isinstance(f, list) and f]
        if name in aliases:
            keep_alias[name] = aliases[name]
            todo.append(aliases[name].get("type", ""))
    return {
        "module": "prismash",
        "messages": [msgs[m] for m in sorted(want)],
        "services": {k: v for k, v in services.items() if k in want},
        "aliases": keep_alias,
        **{sec: list(types[sec].values()) for sec in _TYPE_SECTIONS},
    }


def _cache_dir():
    """
    A directory only this account can write, for the pruned definitions, or
    None. vpp_papi takes files, and one planted under a shared /tmp name by
    another account would be read as this box's API - so the directory must
    be ours and closed to everybody else, or nothing is kept.
    """
    import tempfile
    uid = os.geteuid()
    for d in (os.environ.get("XDG_RUNTIME_DIR", ""),
              os.path.join(tempfile.gettempdir(), f"prisma-papi-{uid}")):
        if not d:
            continue
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            st = os.stat(d)
        except OSError:
            continue
        if st.st_uid == uid and not st.st_mode & 0o077:
            return d
    return None


def definitions_file(files, keep=VTY_MESSAGES, cachedir=None):
    """
    The path of one file of pruned definitions for `files`, made once and
    kept (#529), or None when it cannot be had - the caller then hands
    vpp_papi the files themselves, which is slower and right.

    Kept under a name made of every file's path, size and mtime and of
    `keep`: a package upgrade that rewrites a definition makes a new name,
    so a file for the old one is never read against the new dataplane.
    """
    import hashlib
    try:
        key = hashlib.sha256()
        for f in sorted(files):
            st = os.stat(f)
            key.update(f"{f}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
        key.update(" ".join(sorted(keep)).encode())
    except OSError:
        return None
    d = cachedir or _cache_dir()
    if d is None:
        return None
    path = os.path.join(d, f"prismash-api-{key.hexdigest()[:24]}.json")
    if os.path.exists(path):
        return path
    try:
        apis = []
        for f in files:
            with open(f) as fh:
                apis.append(json.load(fh))
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(prune_definitions(apis, keep), fh)
        os.replace(tmp, path)
    except (OSError, ValueError):
        return None
    return path


def cli_is_question(cmd):
    """Is a CLI line one that only reads? `show ...` is; anything else writes."""
    return cmd.split()[:1] == ["show"]


def client():
    """The dataplane client the VTY uses: one connection, or vat2."""
    return Papi()
