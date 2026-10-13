"""
RADIUS for the box's subscribers (#607): the `aaa` > `radius` hierarchy's
model, and what accel-ppp is given from it.

WHY A HIERARCHY OF ITS OWN. 0.154.0 (#597) held RADIUS inside `pppoe`, one
list of up to four servers and no say in how they were used; what accel-ppp
was given marked no server down, ever (`fail-time=0`), so a dead first server
cost every new subscriber the whole retry budget. The operator asked for
RADIUS beside PPPoE rather than under it - IPoE will use the same servers -
with an order, a choice between failover and load sharing, and timers that
fail over in seconds.

HOW ACCEL-PPP CHOOSES A SERVER, read in its source at the pin
(build/accel-ppp-build.sh, radius/serv.c __rad_server_get): among the servers
that have the port the request needs (auth or acct) and are not inside their
`fail-time`, the LOWEST `backup` level wins; within one level, upstream
took the one with the fewest requests and sessions in flight per `weight`,
and the patch makes it a ROTATION (0.155.1, below). A server is marked
down for `fail-time` seconds after `max-fail` consecutive timeouts; a request
moves to the next server after `max-try` sends to one (radius/req.c
rad_req_send). Upstream holds `backup` to 0 or 1, so a third server shared
the load with the second instead of waiting behind it; build/accel-ppp-ng.
patch makes it a level (`backup=<n>`), which is what makes an ORDER.

SO THE TWO MODES ARE:

  * failover: each server's level is its rank by `priority` (lowest
    first); a request goes to the first server alive, the next only when the
    first is marked down or did not answer `retries` + 1 sends. Two servers
    with one priority share that rank, as round-robin would.
  * round-robin: every server on one level - each new request to the next
    server alive, in turn. Upstream's least-outstanding is not that: with
    nothing in flight its tie-break sent every request to the first server,
    measured on prisma-bench as 7 Access-Requests to 1 over six sequential
    logins; the patch turns a cursor over the servers alive on the level
    (radius/serv.c), and lab/pppoe.sh asserts six sequential logins split
    within one.

Accounting follows the same choice: an Accounting-Request is placed by the
same rule among the servers that take accounting, and a session's Start,
interims and Stop go to the server its Start went to until that server is
marked down.

A SERVER'S ROLE. `role auth` or `role acct` is the other port written as 0,
which accel-ppp reads as "not for that" (radius/serv.c: a server with no
auth_port is skipped for an Access-Request). No accounting server at all is
allowed, and accounting is then off - `show pppoe summary` says so.

SECRETS. Every secret here is a password on the wire: held in the saved
configuration and in aaa.conf (0660, group prisma-cgnat, as pppoe.conf),
never on a screen - `redact()` is what every show and compare passes the text
through.
"""
import ipaddress
import os
import re

from system import is_number

#: The words, at the top of configuration mode and under it.
AAA_HIERARCHY = "aaa"
RADIUS_LEVEL = "radius"

#: The model as the commit wrote it, in the VTY's words (the `aaa` block).
CONF_PATH = os.environ.get("PRISMA_AAA_CONF", "/etc/prisma-routing/aaa.conf")

#: A server's port, the RFC's (2865, 2866).
AUTH_PORT, ACCT_PORT = "1812", "1813"
#: RFC 5176's port for Disconnect and CoA.
DAE_PORT = "3799"
ROLES = ("auth", "acct", "both")
MODES = ("failover", "round-robin")

#: The single-valued settings and what an absent one means. Timers chosen so
#: that a dead first server costs ONE subscriber six seconds - 3 s for the
#: first send, 3 s for the retry - and is then left alone for 30 s: the
#: next subscriber goes straight to the second. A CPE retransmits its PAP or
#: CHAP request for longer than that (pppd: 10 times, 3 s apart), so the
#: session it is dialling survives the failover. Measured in lab/pppoe.sh.
DEFAULTS = {"mode": "failover", "timeout": "3", "retries": "1",
            "dead-time": "30", "nas-identifier": "prisma-routing",
            "interim-interval": "300"}
SETTINGS = ("mode", "timeout", "retries", "dead-time", "nas-identifier",
            "interim-interval")
TIMEOUT_MIN, TIMEOUT_MAX = 1, 60
RETRIES_MAX = 10
DEAD_MIN, DEAD_MAX = 1, 3600
INTERIM_MIN, INTERIM_MAX = 60, 86400
SERVERS_MAX = 8
PRIORITY_MIN, PRIORITY_MAX = 1, 255
#: The patch's table (build/accel-ppp-ng.patch, radius/dm_coa.c).
DAE_CLIENTS_MAX = 16
#: The patched shaper's table of them (PRISMA_PROFILES_MAX).
PROFILES_MAX = 64
_PROFILE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
PROFILE_SAYS = ("rate-profile <name> down <rate> up <rate>: the rates a "
                "Cisco-AVPair sub-qos-policy of that name means, kbit/s or "
                "with K, M or G; 0 for none")

#: A secret: printable ASCII with no space, and none of the characters
#: accel-ppp's file uses to separate fields (`,` in a `server=` line), a
#: comment (`#`), or a quote. `:` is allowed: the DAE line's address and
#: port come before the secret, and accel-ppp splits at the FIRST colon.
_SECRET = re.compile(r'^[A-Za-z0-9!$%&()*+./:;<=>?@\[\]^_{|}~-]{1,128}$')
_WORD = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

NOT_SHOWN = "<set, not shown>"

SECRET_SAYS = ("a secret is 1-128 printable characters with no space, "
               "comma, quote or '#'")
SERVER_SAYS = ("server <address> secret <secret> [auth-port <n>] "
               "[acct-port <n>] [role auth|acct|both] [priority <n>]")
DAE_SAYS = ("dynamic-authorization listen <address> [port <n>], "
            "dynamic-authorization client <address> secret <secret>")
#: The statements, as `?` offers them and the refusal lists them.
SAYS = ("under 'radius': server, mode, timeout, retries, dead-time, "
        "nas-identifier, interim-interval, dynamic-authorization, "
        "rate-profile")
LEVELS_SAY = "under 'aaa' there is 'radius'"
#: Where 0.154.0's statements went, said at the prompt where they are typed.
MOVED_SAYS = ("RADIUS is configured under 'aaa' > 'radius' since #607: "
              "enter 'aaa', then 'radius', and type 'server <address> "
              "secret <secret>' there; PPPoE uses those servers")


def empty():
    """The model of a box with no RADIUS."""
    return {"servers": [], "settings": {},
            "dae": {"listen": "", "port": DAE_PORT, "clients": {}},
            "profiles": {}}


def is_empty(held) -> bool:
    """True when nothing is configured."""
    return held == empty()


def _addr4(word):
    """`word` as a dotted IPv4 address, or None."""
    try:
        return str(ipaddress.IPv4Address(word))
    except (ValueError, ipaddress.AddressValueError):
        return None


def _number(word, lo, hi):
    """`word` as an int within lo..hi, or None."""
    if not is_number(word):
        return None
    n = int(word)
    return n if lo <= n <= hi else None


def setting(held, key):
    """A single-valued setting, or its default."""
    return held["settings"].get(key, DEFAULTS.get(key, ""))


def setting_error(key, value):
    """Why `value` cannot be the setting `key`, or None."""
    if key == "mode":
        if value not in MODES:
            return "mode: failover or round-robin"
    elif key == "timeout":
        if _number(value, TIMEOUT_MIN, TIMEOUT_MAX) is None:
            return (f"timeout: {TIMEOUT_MIN}-{TIMEOUT_MAX} seconds a request "
                    f"waits for an answer")
    elif key == "retries":
        if _number(value, 0, RETRIES_MAX) is None:
            return (f"retries: 0-{RETRIES_MAX} sends to one server after the "
                    f"first, before the next server")
    elif key == "dead-time":
        if _number(value, DEAD_MIN, DEAD_MAX) is None:
            return (f"dead-time: {DEAD_MIN}-{DEAD_MAX} seconds a server that "
                    f"did not answer is left alone")
    elif key == "interim-interval":
        n = _number(value, 0, INTERIM_MAX)
        if n is None or 0 < n < INTERIM_MIN:
            return (f"interim-interval: 0 (none) or "
                    f"{INTERIM_MIN}-{INTERIM_MAX} seconds")
    elif key == "nas-identifier":
        if not _WORD.match(value):
            return "nas-identifier: 1-32 letters, digits, '.', '_' or '-'"
    else:
        return f"{key}: not a setting of 'radius'"
    return None


def server_words(words):
    """
    `<address> secret <s> [auth-port <n>] [acct-port <n>] [role <r>]
    [priority <n>]` as a server dict (priority "" when not given), and why
    not.
    """
    if len(words) < 3 or words[1] != "secret":
        return None, SERVER_SAYS
    addr = _addr4(words[0])
    if not addr:
        return None, f"server: '{words[0]}' is not an IPv4 address"
    if not _SECRET.match(words[2]):
        return None, f"server: {SECRET_SAYS}"
    srv = {"address": addr, "secret": words[2], "auth-port": AUTH_PORT,
           "acct-port": ACCT_PORT, "role": "both", "priority": ""}
    rest = words[3:]
    while rest:
        if len(rest) < 2:
            return None, SERVER_SAYS
        k, v = rest[0], rest[1]
        if k in ("auth-port", "acct-port"):
            if _number(v, 1, 65535) is None:
                return None, f"server: {k} 1-65535"
        elif k == "role":
            if v not in ROLES:
                return None, "server: role auth, acct or both"
        elif k == "priority":
            if _number(v, PRIORITY_MIN, PRIORITY_MAX) is None:
                return None, (f"server: priority {PRIORITY_MIN}-"
                              f"{PRIORITY_MAX}, the lowest tried first")
            v = str(int(v))
        else:
            return None, SERVER_SAYS
        srv[k] = v
        rest = rest[2:]
    return srv, None


def _put_server(held, srv):
    """
    `srv` into the list: the same address replaced in place, keeping its
    priority unless one is given; a new one at the next priority after the
    highest. None, or why not.
    """
    servers = held["servers"]
    for i, r in enumerate(servers):
        if r["address"] == srv["address"]:
            if not srv["priority"]:
                srv["priority"] = r["priority"]
            servers[i] = srv
            return None
    if len(servers) >= SERVERS_MAX:
        return f"at most {SERVERS_MAX} RADIUS servers"
    if not srv["priority"]:
        srv["priority"] = str(max([int(r["priority"]) for r in servers]
                                  + [0]) + 1)
        if int(srv["priority"]) > PRIORITY_MAX:
            return (f"server: no priority left after {PRIORITY_MAX}; give "
                    f"one with 'priority <n>'")
    servers.append(srv)
    return None


def _dae(held, neg, args):
    """`dynamic-authorization ...` applied to `held`; None or why not."""
    d = held["dae"]
    if neg:
        if not args:
            d.update(empty()["dae"])
            d["clients"] = {}
            return None
        if args == ["listen"]:
            d["listen"], d["port"] = "", DAE_PORT
            return None
        if len(args) == 2 and args[0] == "client":
            a = _addr4(args[1])
            if not a or a not in d["clients"]:
                return (f"no dynamic-authorization client: {args[1]} is not "
                        f"a client here")
            del d["clients"][a]
            return None
        return ("no dynamic-authorization [listen | client <address>]")
    if args[:1] == ["listen"]:
        if len(args) not in (2, 4) or not _addr4(args[1]) or (
                len(args) == 4 and (args[2] != "port"
                                    or _number(args[3], 1, 65535) is None)):
            return ("dynamic-authorization listen <address> [port <n>]: an "
                    "address of this box, the port 1-65535 (3799 when unset)")
        d["listen"] = _addr4(args[1])
        d["port"] = str(int(args[3])) if len(args) == 4 else DAE_PORT
        return None
    if args[:1] == ["client"]:
        if len(args) != 4 or args[2] != "secret" or not _addr4(args[1]):
            return ("dynamic-authorization client <address> secret <secret>"
                    ": the server that may send Disconnect and CoA")
        if not _SECRET.match(args[3]):
            return f"dynamic-authorization client: {SECRET_SAYS}"
        a = _addr4(args[1])
        if a not in d["clients"] and len(d["clients"]) >= DAE_CLIENTS_MAX:
            return f"at most {DAE_CLIENTS_MAX} dynamic-authorization clients"
        d["clients"][a] = args[3]
        return None
    return DAE_SAYS


def stage(held, words):
    """
    One `aaa radius ...` or `no aaa ...` statement - the model's words for
    what is typed under `aaa` > `radius` - applied to `held`; None, or why
    not. The file's rule and the prompt's, so a saved file cannot hold what
    the prompt refuses.
    """
    w = list(words)
    neg = w[:1] == ["no"]
    if neg:
        w = w[1:]
    if w[:1] != [AAA_HIERARCHY]:
        return LEVELS_SAY
    w = w[1:]
    if neg and (not w or w == [RADIUS_LEVEL]):
        held.clear()
        held.update(empty())
        return None
    if w[:1] != [RADIUS_LEVEL]:
        return LEVELS_SAY
    w = w[1:]
    if not w:
        return SAYS
    kw, args = w[0], w[1:]
    if kw == "server":
        if neg:
            a = _addr4(args[0]) if len(args) == 1 else None
            if not a:
                return "no server <address>"
            if a not in [r["address"] for r in held["servers"]]:
                return f"no server: {a} is not a server here"
            held["servers"][:] = [r for r in held["servers"]
                                  if r["address"] != a]
            return None
        srv, why = server_words(args)
        if why:
            return why
        return _put_server(held, srv)
    if kw in SETTINGS:
        if neg:
            if args:
                return f"no {kw}"
            held["settings"].pop(kw, None)
            return None
        if len(args) != 1:
            return f"{kw} <value>"
        why = setting_error(kw, args[0])
        if why:
            return why
        held["settings"][kw] = args[0]
        return None
    if kw == "dynamic-authorization":
        return _dae(held, neg, args)
    if kw == "rate-profile":
        return _profile(held, neg, args)
    return SAYS


def profile_rate(word):
    """A profile's rate - kbit/s, K, M or G, or 0 - as kbit/s, or None."""
    if word == "0":
        return 0
    m = re.match(r"^([0-9]{1,8})([KMG]?)$", word or "", re.IGNORECASE)
    if not m:
        return None
    n = int(m.group(1)) * {"": 1, "K": 1, "M": 1000,
                           "G": 1000000}[m.group(2).upper()]
    return n if n <= 10000000 else None


def _profile(held, neg, args):
    """`rate-profile ...` applied to `held`; None or why not."""
    p = held["profiles"]
    if neg:
        if len(args) != 1:
            return "no rate-profile <name>"
        if args[0] not in p:
            return f"no rate-profile: {args[0]} is not a profile here"
        del p[args[0]]
        return None
    if len(args) != 5 or args[1] != "down" or args[3] != "up" \
            or not _PROFILE.match(args[0]) or profile_rate(args[2]) is None \
            or profile_rate(args[4]) is None:
        return PROFILE_SAYS
    if args[0] not in p and len(p) >= PROFILES_MAX:
        return f"at most {PROFILES_MAX} rate profiles"
    p[args[0]] = {"down": args[2], "up": args[4]}
    return None


def migrate_pppoe(held, words):
    """
    A 0.154.0 `pppoe radius ...` statement (`words` after `pppoe`) applied
    to `held` as its `aaa radius` equivalent (#607): the statement it became,
    in the model's words, or None when it is not one of them. `server` keeps
    its secret and ports and takes the next priority, so the order the file
    listed them in is the failover order.
    """
    if words[:1] != ["radius"] or len(words) < 2:
        return None
    if words[1] == "server":
        new = ["aaa", "radius"] + words[1:]
    elif words[1] in ("nas-identifier", "interim-interval"):
        new = ["aaa", "radius"] + words[1:]
    else:
        return None
    if stage(held, new) is not None:
        return None
    return " ".join(new)


def ordered(held):
    """The servers by priority, then by the order they were typed in."""
    return sorted(held["servers"], key=lambda r: int(r["priority"]))


def server_line(r):
    """A server's statement under `radius`, every non-default word."""
    line = f"server {r['address']} secret {r['secret']}"
    for k, dflt in (("auth-port", AUTH_PORT), ("acct-port", ACCT_PORT)):
        if r[k] != dflt:
            line += f" {k} {r[k]}"
    if r["role"] != "both":
        line += f" role {r['role']}"
    return line + f" priority {r['priority']}"


def body(held):
    """The statements under `radius`, in the order they are written."""
    out = [server_line(r) for r in ordered(held)]
    s = held["settings"]
    out += [f"{k} {s[k]}" for k in SETTINGS if k in s]
    d = held["dae"]
    if d["listen"]:
        out.append(f"dynamic-authorization listen {d['listen']}"
                   + (f" port {d['port']}" if d["port"] != DAE_PORT else ""))
    out += [f"dynamic-authorization client {a} secret {d['clients'][a]}"
            for a in sorted(d["clients"],
                            key=lambda x: ipaddress.IPv4Address(x))]
    out += [f"rate-profile {n} down {held['profiles'][n]['down']} up "
            f"{held['profiles'][n]['up']}" for n in sorted(held["profiles"])]
    return out


def shaper_profiles(held):
    """
    The rate profiles as the patched shaper reads them in [shaper] (#607):
    `cisco-profile=<name>,<down kbit>,<up kbit>`.
    """
    return [f"cisco-profile={n},{profile_rate(p['down'])},"
            f"{profile_rate(p['up'])}"
            for n, p in sorted(held["profiles"].items())]


def nest(held):
    """The `aaa` block a file carries, or nothing when none is set."""
    b = body(held)
    if not b:
        return []
    return ([AAA_HIERARCHY, f" {RADIUS_LEVEL}"] + [f"  {x}" for x in b]
            + ["  exit", " exit"])


def redact(lines):
    """
    Every RADIUS secret, never on a screen: a server's and a dynamic-
    authorization client's, nested or flat (`aaa radius server ...`, which
    is how a compare's diff and the model write them).
    """
    out = []
    for line in lines:
        w = line.split()
        pre = line[:len(line) - len(line.lstrip())]
        tail = w
        while tail[:1] in (["no"], [AAA_HIERARCHY], [RADIUS_LEVEL]):
            tail = tail[1:]
        if (tail[:1] == ["server"] or tail[:2] == ["dynamic-authorization",
                                                  "client"]) \
                and "secret" in tail \
                and " ".join(w[w.index("secret") + 1:][:3]) != NOT_SHOWN:
            # (Not twice: a line another redact() already blanked is left
            # as it is, or "<set," is blanked again behind "not shown>".)
            i = w.index("secret")
            out.append(pre + " ".join(w[:i + 1] + [NOT_SHOWN] + w[i + 2:]))
        else:
            out.append(line)
    return out


def auth_servers(held):
    """The servers an Access-Request may go to, by priority."""
    return [r for r in ordered(held) if r["role"] in ("auth", "both")]


def acct_servers(held):
    """The servers an Accounting-Request may go to, by priority."""
    return [r for r in ordered(held) if r["role"] in ("acct", "both")]


def check(held):
    """
    What makes the block one accel-ppp cannot use, as rows; empty when it
    can. Asked at commit, PPPoE or not: a listener with no client is a
    listener nobody can sign a request for.
    """
    why = []
    d = held["dae"]
    if d["listen"] and not d["clients"]:
        why.append("aaa radius: dynamic-authorization listens and has no "
                   "client: add 'dynamic-authorization client <address> "
                   "secret <secret>'")
    if d["clients"] and not d["listen"]:
        why.append("aaa radius: dynamic-authorization clients and nowhere "
                   "to listen: add 'dynamic-authorization listen <address>'")
    if held["servers"] and not auth_servers(held):
        why.append("aaa radius: no server takes authentication: give one "
                   "'role auth' or 'role both'")
    return why


def _levels(held):
    """{address: accel-ppp backup level}: 0 for the first priority."""
    if setting(held, "mode") == "round-robin":
        return {r["address"]: 0 for r in held["servers"]}
    ranks = sorted({int(r["priority"]) for r in held["servers"]})
    return {r["address"]: ranks.index(int(r["priority"]))
            for r in held["servers"]}


def accel_radius(held, gateway):
    """
    accel-ppp.conf's [radius] section, generated; [] with no server. What
    each line is for, as accel-ppp reads it at the pin:

      timeout, max-try   seconds per send, sends per server (retries + 1);
      fail-time, max-fail  on each server: marked down for dead-time after
                         max-try consecutive timeouts - 0.154.0 wrote 0, which
                         marks nothing down, ever;
      acct-timeout=0     a session is never ENDED because accounting went
                         unanswered: accel-ppp's default, 3, terminated every
                         session whose interim got no answer for 3 s - every
                         subscriber, at a RADIUS outage longer than one
                         interim's retries (radius/acct.c rad_acct_timeout);
      backup=<n>         the failover level (build/accel-ppp-ng.patch);
      dae-server, dae-client  the Disconnect/CoA listener and who may use it.
    """
    if not held["servers"]:
        return []
    tries = int(setting(held, "retries")) + 1
    dead = setting(held, "dead-time")
    # Two dictionaries: accel-ppp's own, and Prisma's (vendor 67033, the
    # rate limits) - accel-ppp reads every `dictionary=` line (radius.c).
    out = ["[radius]", "dictionary=/usr/share/accel-ppp/radius/dictionary",
           "dictionary=/usr/share/prisma-bng/dictionary.prisma",
           f"nas-identifier={setting(held, 'nas-identifier')}",
           f"gw-ip-address={gateway}", "verbose=1",
           f"timeout={setting(held, 'timeout')}", f"max-try={tries}",
           "acct-timeout=0",
           f"acct-interim-interval={setting(held, 'interim-interval')}"]
    levels = _levels(held)
    for r in ordered(held):
        auth = r["auth-port"] if r["role"] in ("auth", "both") else "0"
        acct = r["acct-port"] if r["role"] in ("acct", "both") else "0"
        line = (f"server={r['address']},{r['secret']},auth-port={auth},"
                f"acct-port={acct},req-limit=0,fail-time={dead},"
                f"max-fail={tries}")
        if levels[r["address"]]:
            line += f",backup={levels[r['address']]}"
        out.append(line)
    d = held["dae"]
    if d["listen"] and d["clients"]:
        first = sorted(d["clients"],
                       key=lambda x: ipaddress.IPv4Address(x))
        # accel-ppp will not open the listener without ITS secret; with a
        # client list (the patch) a request is checked against its sender's
        # own, so this one signs nothing a client did not send.
        out.append(f"dae-server={d['listen']}:{d['port']},"
                   f"{d['clients'][first[0]]}")
        out += [f"dae-client={a},{d['clients'][a]}" for a in first]
    return out


def dae_words(held):
    """What the listener is, for a restart-or-reload decision and a show."""
    d = held["dae"]
    return (d["listen"], d["port"], tuple(sorted(d["clients"].items())))


def _write(path, text):
    """`text` into `path` through a rename, 0660 (pppoe._write's reasons)."""
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o660)
    os.replace(tmp, path)


def write_conf(held, path=None):
    """The model into aaa.conf, or the file removed; (True, what) or why."""
    path = path or CONF_PATH
    try:
        if is_empty(held):
            if os.path.exists(path):
                os.unlink(path)
            return True, "not configured"
        _write(path, "\n".join(nest(held)) + "\n")
    except OSError as e:
        return False, f"could not write {e.filename}: {e.strerror}"
    return True, "written"


def read_conf(path=None, legacy=None):
    """
    The model aaa.conf holds. With no aaa.conf, what a 0.154.0 pppoe.conf
    (`legacy`) held under `radius` - a box upgraded and not yet committed
    since holds its RADIUS there, and reading it as "no RADIUS" would make
    the first commit after the upgrade look like a change it is not.
    """
    held = empty()
    try:
        with open(path or CONF_PATH) as f:
            text = f.read()
    except OSError:
        try:
            with open(legacy) as f:
                text = f.read()
        except (OSError, TypeError):
            return held
        for line in text.splitlines():
            migrate_pppoe(held, line.split())
        return held
    for line in text.splitlines():
        s = line.strip()
        if not s or s in (AAA_HIERARCHY, RADIUS_LEVEL, "exit") \
                or s.startswith("#"):
            continue
        stage(held, [AAA_HIERARCHY, RADIUS_LEVEL] + s.split())
    return held


# --------------------------------------------------------------- operation

def server_states(stat):
    """
    {address: "active" | "failed"} out of accel-cmd's `show stat`, whose
    RADIUS part is one block a server: `radius(1, 192.0.2.1):` then
    `  state: active`.
    """
    out, cur = {}, None
    for line in stat.splitlines():
        m = re.match(r"^radius\(\d+,\s*([0-9.]+)\):", line.strip())
        if m:
            cur = m.group(1)
            continue
        s = line.strip()
        if cur and s.startswith("state:"):
            out[cur] = s.split(":", 1)[1].strip()
            cur = None
    return out


def summary_rows(held, states=None):
    """`show pppoe summary`'s RADIUS rows: the mode, each server, accounting."""
    if not held["servers"]:
        return []
    states = states or {}
    mode = setting(held, "mode")
    rows = [f"{'radius':<22}{mode}, timeout {setting(held, 'timeout')} s, "
            f"{setting(held, 'retries')} retr{'y' if setting(held, 'retries') == '1' else 'ies'}, "
            f"dead-time {setting(held, 'dead-time')} s"]
    for r in ordered(held):
        role = {"both": "auth+acct", "auth": "auth", "acct": "acct"}[r["role"]]
        where = f"priority {r['priority']}" if mode == "failover" else ""
        rows.append(f"  {r['address']:<20}{role:<10}{where:<13}"
                    f"{states.get(r['address'], '')}".rstrip())
    if not acct_servers(held):
        rows.append(f"{'accounting':<22}off: no server takes accounting")
    d = held["dae"]
    if d["listen"]:
        rows.append(f"{'disconnect/CoA':<22}{d['listen']} port {d['port']}, "
                    f"{len(d['clients'])} client(s)")
    return rows
