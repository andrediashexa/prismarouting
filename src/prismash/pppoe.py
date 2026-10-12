"""
PPPoE subscriber termination (#597, BNG phase 1): the `pppoe` hierarchy's
model, and everything generated from it for the prisma-bng container.

THE CONFIGURATION IS THE CANDIDATE'S. An operator types `pppoe` and the
statements under it; commit, rollback, `commit confirmed` and the audit trail
apply as to any other statement. What the container runs - accel-ppp.conf,
the local users' chap-secrets, access.env for its plumbing - is GENERATED
here from the committed model and written into /etc/prisma-routing/bng,
which the container mounts read-only. Nobody edits those files; a hand edit
is overwritten at the next commit, and `show running-config` reads the model
back from pppoe.conf, never from them.

WHERE THE PIECES RUN:

  * prismash (mgmt container): this module; writes the files at commit, and
    asks the host's doors to start, restart or stop the container
    (BNG_CTL, appliance/prisma-bng);
  * prisma-bng (its own container): accel-pppd - discovery, LCP, PAP/CHAP/
    MS-CHAPv2, IPCP, RADIUS auth and accounting - driving VPP's pppoe plugin
    over the binary API; appliance/prisma-bng-entrypoint lays the plumbing,
    appliance/prisma-bng-session attaches each session to the CGNAT.

SECRETS. A RADIUS shared secret and a local user's password are passwords on
the wire, held like SNMP's community: in the saved configuration and in
pppoe.conf (0660, group prisma-cgnat, as prisma-routing.conf is), never on a
screen - `redact()` is what every show and compare passes the text through.
"""
import ipaddress
import os
import re

from system import is_number

#: The hierarchy's word, at the top of configuration mode.
PPPOE_HIERARCHY = "pppoe"

#: The model as the commit wrote it, in the VTY's words (the `pppoe` block).
CONF_PATH = os.environ.get("PRISMA_PPPOE_CONF",
                           "/etc/prisma-routing/pppoe.conf")
#: What the container mounts: the files generated from the model.
BNG_DIR = os.environ.get("PRISMA_BNG_DIR", "/etc/prisma-routing/bng")
#: The host's lifecycle script, which the doors run (doors.py BNG_DOORS).
BNG_CTL_HOST = "/usr/lib/prisma-routing/prisma-bng"
BNG_CTL = os.environ.get("PRISMA_BNG_CTL", BNG_CTL_HOST)

#: The plumbing's Linux names, which accel-ppp.conf binds and the entrypoint
#: creates: the TAP VPP's pppoe plugin punts discovery and control to, and one
#: VLAN device on it per tagged access interface.
CP_LINUX = "pppoe-cp"
#: Where accel-ppp runs a session's ip-up, in the container.
SESSION_HOOK = "/usr/lib/prisma-bng/session"
#: accel-ppp's own logs, on the log volume the container mounts.
LOG_DIR = "/var/log/prisma-routing/bng"
#: The RADIUS dictionary the image installs.
DICTIONARY = "/usr/share/accel-ppp/radius/dictionary"

AUTH_METHODS = ("pap", "chap", "mschap-v2")
#: accel-ppp's module for each.
AUTH_MODULE = {"pap": "auth_pap", "chap": "auth_chap_md5",
               "mschap-v2": "auth_mschap_v2"}

#: Settings that take one value, with the bounds each is checked against.
#: The defaults are what an absent statement means; they are never written.
DEFAULTS = {"mtu": "1492", "interim-interval": "300",
            "authentication": "pap chap mschap-v2",
            "ac-name": "prisma-routing", "nas-identifier": "prisma-routing"}
DNS_MAX = 2
RADIUS_MAX = 4
POOLS_MAX = 16
INTERFACES_MAX = 16
USERS_MAX = 1000
SESSION_LIMIT_MAX = 1000000
MTU_MIN, MTU_MAX = 576, 1500
MSS_MIN, MSS_MAX = 536, 1460
INTERIM_MIN, INTERIM_MAX = 60, 86400

#: A secret or password: printable ASCII with no space, and none of the
#: characters accel-ppp's files use to separate fields (`,` in a `server=`
#: line, `"` in chap-secrets, `#` a comment) or a shell would read.
_SECRET = re.compile(r'^[A-Za-z0-9!$%&()*+./:;<=>?@\[\]^_{|}~-]{1,128}$')
_NAME = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_WORD = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
#: An interface name as the dataplane has it: VPP's names carry '/', and a
#: sub-interface is `<parent>.<n>`.
_IFNAME = re.compile(r"^[A-Za-z0-9._/:-]{1,63}$")

NOT_SHOWN = "<set, not shown>"


def empty():
    """The model of a box with no PPPoE."""
    return {"interfaces": [], "gateway": "", "pools": [], "dns": [],
            "radius": [], "settings": {}, "users": {}}


def is_empty(held) -> bool:
    """True when nothing is configured: the container does not run."""
    return held == empty()


def _addr4(word):
    """`word` as a dotted IPv4 address, or None."""
    try:
        return str(ipaddress.IPv4Address(word))
    except (ValueError, ipaddress.AddressValueError):
        return None


def pool_error(word):
    """Why `word` is not an address pool, or None: a prefix or a range."""
    if "/" in word:
        try:
            net = ipaddress.IPv4Network(word, strict=True)
        except ValueError:
            return f"address-pool: '{word}' is not an IPv4 prefix"
        if net.prefixlen > 30:
            return "address-pool: a prefix of /30 or shorter"
        return None
    first, _, last = word.partition("-")
    a, b = _addr4(first), _addr4(last)
    if not a or not b:
        return (f"address-pool: '{word}' is neither a prefix nor a "
                f"range <first>-<last>")
    if int(ipaddress.IPv4Address(b)) < int(ipaddress.IPv4Address(a)):
        return "address-pool: the range ends before it starts"
    return None


def _number(word, lo, hi):
    """`word` as an int within lo..hi, or None."""
    if not is_number(word):
        return None
    n = int(word)
    return n if lo <= n <= hi else None


def _radius_server(words):
    """
    `<address> secret <s> [auth-port <n>] [acct-port <n>]` as a server dict,
    and why not.
    """
    if len(words) < 3 or words[1] != "secret":
        return None, ("radius server <address> secret <secret> "
                      "[auth-port <n>] [acct-port <n>]")
    addr = _addr4(words[0])
    if not addr:
        return None, f"radius server: '{words[0]}' is not an IPv4 address"
    if not _SECRET.match(words[2]):
        return None, ("radius server: a secret is 1-128 printable characters "
                      "with no space, comma, quote or '#'")
    srv = {"address": addr, "secret": words[2], "auth-port": "1812",
           "acct-port": "1813"}
    rest = words[3:]
    while rest:
        if len(rest) < 2 or rest[0] not in ("auth-port", "acct-port") \
                or _number(rest[1], 1, 65535) is None:
            return None, ("radius server: after the secret, auth-port <n> "
                          "and acct-port <n>, 1-65535")
        srv[rest[0]] = rest[1]
        rest = rest[2:]
    return srv, None


def setting_error(key, value):
    """Why `value` cannot be the single-valued setting `key`, or None."""
    if key == "mtu":
        if _number(value, MTU_MIN, MTU_MAX) is None:
            return f"mtu: {MTU_MIN}-{MTU_MAX}, the PPP MTU and MRU"
    elif key == "tcp-mss":
        if _number(value, MSS_MIN, MSS_MAX) is None:
            return f"tcp-mss: {MSS_MIN}-{MSS_MAX}"
    elif key == "session-limit":
        if _number(value, 1, SESSION_LIMIT_MAX) is None:
            return f"session-limit: 1-{SESSION_LIMIT_MAX}"
    elif key == "interim-interval":
        n = _number(value, 0, INTERIM_MAX)
        if n is None or 0 < n < INTERIM_MIN:
            return (f"radius interim-interval: 0 (none) or "
                    f"{INTERIM_MIN}-{INTERIM_MAX} seconds")
    elif key in ("ac-name", "nas-identifier"):
        if not _WORD.match(value):
            return f"{key}: 1-32 letters, digits, '.', '_' or '-'"
    elif key == "authentication":
        methods = value.split()
        if not methods or any(m not in AUTH_METHODS for m in methods) \
                or len(set(methods)) != len(methods):
            return "authentication: one or more of " + ", ".join(AUTH_METHODS)
    else:
        return f"{key}: not a setting of 'pppoe'"
    return None


#: The statements, as `?` offers them and the refusal lists them.
STATEMENTS = ("interface", "gateway", "address-pool", "dns", "radius",
              "authentication", "session-limit", "mtu", "tcp-mss", "ac-name",
              "local-user")
SAYS = ("under 'pppoe': interface, gateway, address-pool, dns, radius, "
        "authentication, session-limit, mtu, tcp-mss, ac-name, local-user")


def stage(held, words):
    """
    One `pppoe ...` or `no pppoe ...` statement - the model's words for
    what is typed under the `pppoe` level - applied to `held`; None, or why
    not. The file's rule and the prompt's, so a saved file cannot hold what
    the prompt refuses.
    """
    w = list(words)
    neg = w[:1] == ["no"]
    if neg:
        w = w[1:]
    if w[:1] != [PPPOE_HIERARCHY]:
        return SAYS
    w = w[1:]
    if neg and not w:
        held.clear()
        held.update(empty())
        return None
    if not w:
        return SAYS
    kw, args = w[0], w[1:]
    s = held["settings"]
    if kw == "interface":
        if len(args) != 1 or not _IFNAME.match(args[0]):
            return "interface <name>: an interface the dataplane has"
        if neg:
            if args[0] not in held["interfaces"]:
                return f"no interface: {args[0]} is not an access interface"
            held["interfaces"].remove(args[0])
        elif args[0] not in held["interfaces"]:
            if len(held["interfaces"]) >= INTERFACES_MAX:
                return f"at most {INTERFACES_MAX} access interfaces"
            held["interfaces"].append(args[0])
        return None
    if kw == "gateway":
        if neg:
            held["gateway"] = ""
            return None
        if len(args) != 1 or not _addr4(args[0]):
            return "gateway <address>: the box's address on every session"
        held["gateway"] = _addr4(args[0])
        return None
    if kw == "address-pool":
        if len(args) != 1:
            return "address-pool <prefix> | <first>-<last>"
        why = pool_error(args[0])
        if why:
            return why
        if neg:
            if args[0] not in held["pools"]:
                return f"no address-pool: {args[0]} is not a pool here"
            held["pools"].remove(args[0])
        elif args[0] not in held["pools"]:
            if len(held["pools"]) >= POOLS_MAX:
                return f"at most {POOLS_MAX} address pools"
            held["pools"].append(args[0])
        return None
    if kw == "dns":
        if len(args) != 1 or not _addr4(args[0]):
            return "dns <address>: an IPv4 resolver given to the subscriber"
        a = _addr4(args[0])
        if neg:
            if a not in held["dns"]:
                return f"no dns: {a} is not a resolver here"
            held["dns"].remove(a)
        elif a not in held["dns"]:
            if len(held["dns"]) >= DNS_MAX:
                return f"at most {DNS_MAX} resolvers: IPCP carries two"
            held["dns"].append(a)
        return None
    if kw == "radius":
        if args[:1] == ["server"]:
            if neg:
                a = _addr4(args[1]) if len(args) == 2 else None
                if not a:
                    return "no radius server <address>"
                if a not in [r["address"] for r in held["radius"]]:
                    return f"no radius server: {a} is not a server here"
                held["radius"][:] = [r for r in held["radius"]
                                     if r["address"] != a]
                return None
            srv, why = _radius_server(args[1:])
            if why:
                return why
            for i, r in enumerate(held["radius"]):
                if r["address"] == srv["address"]:
                    held["radius"][i] = srv
                    return None
            if len(held["radius"]) >= RADIUS_MAX:
                return f"at most {RADIUS_MAX} RADIUS servers"
            held["radius"].append(srv)
            return None
        if args[:1] in (["nas-identifier"], ["interim-interval"]):
            key = args[0]
            if neg:
                if len(args) != 1:
                    return f"no radius {key}"
                s.pop(key, None)
                return None
            if len(args) != 2:
                return f"radius {key} <value>"
            why = setting_error(key, args[1])
            if why:
                return why
            s[key] = args[1]
            return None
        return ("radius server <address> secret <secret> ..., radius "
                "nas-identifier <name>, radius interim-interval <seconds>")
    if kw in ("authentication", "session-limit", "mtu", "tcp-mss", "ac-name"):
        if neg:
            if args:
                return f"no {kw}"
            s.pop(kw, None)
            return None
        if not args or (kw != "authentication" and len(args) != 1):
            return f"{kw} <value>"
        why = setting_error(kw, " ".join(args))
        if why:
            return why
        s[kw] = " ".join(args)
        return None
    if kw == "local-user":
        if neg:
            if len(args) != 1:
                return "no local-user <name>"
            if args[0] not in held["users"]:
                return f"no local-user: {args[0]} is not a user here"
            del held["users"][args[0]]
            return None
        if len(args) != 3 or args[1] != "password":
            return "local-user <name> password <password>"
        if not _NAME.match(args[0]):
            return "local-user: a name of 1-64 letters, digits, '.', '_', '@', '-'"
        if not _SECRET.match(args[2]):
            return ("local-user: a password is 1-128 printable characters "
                    "with no space, comma, quote or '#'")
        if args[0] not in held["users"] and len(held["users"]) >= USERS_MAX:
            return f"at most {USERS_MAX} local users; RADIUS is for more"
        held["users"][args[0]] = args[2]
        return None
    return SAYS


def body(held):
    """The statements of the block, in the order they are written."""
    out = [f"interface {i}" for i in held["interfaces"]]
    if held["gateway"]:
        out.append(f"gateway {held['gateway']}")
    out += [f"address-pool {p}" for p in held["pools"]]
    out += [f"dns {a}" for a in held["dns"]]
    for r in held["radius"]:
        line = f"radius server {r['address']} secret {r['secret']}"
        for k in ("auth-port", "acct-port"):
            if r[k] != {"auth-port": "1812", "acct-port": "1813"}[k]:
                line += f" {k} {r[k]}"
        out.append(line)
    s = held["settings"]
    for k in ("nas-identifier", "interim-interval"):
        if k in s:
            out.append(f"radius {k} {s[k]}")
    for k in ("authentication", "session-limit", "mtu", "tcp-mss", "ac-name"):
        if k in s:
            out.append(f"{k} {s[k]}")
    out += [f"local-user {n} password {held['users'][n]}"
            for n in sorted(held["users"])]
    return out


def nest(held):
    """The `pppoe` block a file carries, or nothing when none is set."""
    b = body(held)
    if not b:
        return []
    return [PPPOE_HIERARCHY] + [f" {x}" for x in b] + [" exit"]


def redact(lines):
    """A RADIUS secret and a local user's password, never on a screen."""
    out = []
    for line in lines:
        w = line.split()
        pre = line[:len(line) - len(line.lstrip())]
        at = 1 if w[:1] == [PPPOE_HIERARCHY] else 0
        tail = w[at:]
        if tail[:2] == ["radius", "server"] and "secret" in tail:
            i = w.index("secret")
            out.append(pre + " ".join(w[:i + 1] + [NOT_SHOWN] + w[i + 2:]))
        elif tail[:1] == ["local-user"] and "password" in tail:
            i = w.index("password")
            out.append(pre + " ".join(w[:i + 1] + [NOT_SHOWN] + w[i + 2:]))
        else:
            out.append(line)
    return out


def setting(held, key):
    """A single-valued setting, or its default."""
    return held["settings"].get(key, DEFAULTS.get(key, ""))


def check(held):
    """
    What makes a configuration one the container cannot run, as rows; empty
    when it can. Asked at commit, where the operator is: a container started
    on a configuration accel-ppp cannot use is a box that answers no PADI and
    says nothing.
    """
    if is_empty(held):
        return []
    why = []
    if not held["interfaces"]:
        why.append("pppoe: no access interface ('interface <name>')")
    if not held["gateway"]:
        why.append("pppoe: no gateway ('gateway <address>'), the address the "
                   "subscribers see the box at")
    if not held["radius"] and not held["users"]:
        why.append("pppoe: nobody to authenticate against: a RADIUS server "
                   "or, for a lab, a local-user")
    if not held["pools"] and not held["radius"]:
        why.append("pppoe: no address-pool, and no RADIUS to give a "
                   "Framed-IP-Address")
    return why


def access(held, vlans):
    """
    The access interfaces as the plumbing needs them, [(name, parent, vlan)],
    and why not. `vlans` is the configuration's {sub-interface: {"outer",
    "inner"}}: a sub-interface carries ONE tag here, because VPP's pppoe
    plugin parses one (docs/bng.md 1.2) - QinQ is a later step.

    ONE CARD. The plugin has one control-plane interface for the whole box and
    answers every discovery from the card the subscriber came in on; the TAP
    takes that card's MAC so accel-ppp accepts the PADR addressed to it (#170,
    measured). Interfaces on two cards would need two MACs on one TAP, so they
    are refused here rather than half-working.
    """
    out, parents = [], set()
    for name in held["interfaces"]:
        if "." in name and is_number(name.rsplit(".", 1)[1]):
            parent = name.rsplit(".", 1)[0]
            tag = vlans.get(name)
            if not tag:
                return [], (f"pppoe: {name} is a sub-interface with no "
                            f"'vlan <id>' in its block")
            if tag.get("inner"):
                return [], (f"pppoe: {name} has two tags; VPP's PPPoE "
                            f"parses one (QinQ is not in this release)")
            vlan = int(tag["outer"])
        else:
            parent, vlan = name, 0
        parents.add(parent)
        out.append((name, parent, vlan))
    if len(parents) > 1:
        return [], ("pppoe: every access interface on one card: the "
                    "dataplane's PPPoE answers from one MAC ("
                    + ", ".join(sorted(parents)) + ")")
    vlan_ids = [v for _, _, v in out]
    if len(set(vlan_ids)) != len(vlan_ids):
        return [], "pppoe: two access interfaces with the same VLAN"
    return out, None


def cp_name(vlan):
    """The Linux device accel-ppp binds for an access VLAN (0: untagged)."""
    return CP_LINUX if not vlan else f"{CP_LINUX}.{vlan}"


def accel_conf(held, acc):
    """
    accel-ppp.conf, generated. `acc` is access()'s list. The CLI section is
    not here: the entrypoint adds it with a password made at each start.
    """
    auth = setting(held, "authentication").split()
    mods = ["log_file", "vyos_vpp", "pppoe"] + [AUTH_MODULE[a] for a in auth]
    mods.append("radius" if held["radius"] else "chap-secrets")
    if held["pools"]:
        mods.append("ippool")
    mods.append("pppd_compat")
    mtu = setting(held, "mtu")
    out = ["# GENERATED by prismash from the committed configuration (#597).",
           "# Do not edit: the next commit rewrites it, and 'show",
           "# running-config' never reads it.",
           "", "[modules]"] + mods + [
        "", "[core]", f"log-error={LOG_DIR}/accel-ppp-error.log",
        "thread-count=4",
        "", "[common]", "single-session=replace"]
    if "session-limit" in held["settings"]:
        out.append(f"max-sessions={held['settings']['session-limit']}")
    out += ["", "[ppp]", "verbose=1", f"mtu={mtu}", f"mru={mtu}",
            "ipv4=require", "ipv6=deny", "lcp-echo-interval=20",
            "lcp-echo-failure=3",
            "", "[pppoe]", "verbose=1",
            f"ac-name={setting(held, 'ac-name')}"]
    out += [f"interface={cp_name(v)},vpp-cp=enable" for _, _, v in acc]
    if held["dns"]:
        out += ["", "[dns]"] + [f"dns{i + 1}={a}"
                                for i, a in enumerate(held["dns"])]
    if held["radius"]:
        out += ["", "[radius]", f"dictionary={DICTIONARY}",
                f"nas-identifier={setting(held, 'nas-identifier')}",
                f"gw-ip-address={held['gateway']}", "verbose=1",
                f"acct-interim-interval={setting(held, 'interim-interval')}"]
        out += [f"server={r['address']},{r['secret']},auth-port="
                f"{r['auth-port']},acct-port={r['acct-port']},req-limit=0,"
                f"fail-time=0" for r in held["radius"]]
    else:
        out += ["", "[chap-secrets]", f"gw-ip-address={held['gateway']}",
                f"chap-secrets={BNG_DIR}/chap-secrets"]
    if held["pools"]:
        out += ["", "[ip-pool]", f"gw-ip-address={held['gateway']}"]
        out += held["pools"]
    out += ["", "[pppd-compat]", f"ip-up={SESSION_HOOK}", "verbose=1",
            "", "[log]", f"log-file={LOG_DIR}/accel-ppp.log", "level=3",
            "copy=1", ""]
    return "\n".join(out)


def chap_secrets(held):
    """The local users, in the format accel-ppp's chap-secrets reads."""
    lines = ["# GENERATED by prismash (#597): the 'local-user' statements."]
    lines += [f'"{n}" * "{held["users"][n]}" *' for n in sorted(held["users"])]
    return "\n".join(lines) + "\n"


def access_env(held, acc):
    """access.env: what the entrypoint and the session hook read."""
    lines = ["# GENERATED by prismash (#597): the plumbing and the hook.",
             f"PARENT={acc[0][1] if acc else ''}",
             "VLANS=\"" + " ".join(str(v) for _, _, v in acc) + "\"",
             f"MTU={setting(held, 'mtu')}"]
    if "tcp-mss" in held["settings"]:
        lines.append(f"TCP_MSS={held['settings']['tcp-mss']}")
    return "\n".join(lines) + "\n"


def _write(path, text):
    """
    `text` into `path` through a rename, so a reader never sees half; 0660,
    the mode of everything an operator's commit writes in /etc/prisma-routing
    (appliance/install.sh): the directory is setgid prisma-cgnat, so the group
    is the configuring accounts', and an account that may configure has to be
    able to read back what another one committed - a pppoe.conf only its
    writer could read would be read as "no PPPoE" by the next administrator,
    whose commit would then remove it. Never world-readable: it holds the
    RADIUS secret, as prisma-routing.conf holds SNMP's community.
    """
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o660)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o660)
    os.replace(tmp, path)


def write_files(held, acc, conf=None, bng=None):
    """
    The model into pppoe.conf and, when it is set, the container's files into
    BNG_DIR; with nothing set, both removed. (True, what) or (False, why).
    """
    conf = conf or CONF_PATH
    bng = bng or BNG_DIR
    try:
        if is_empty(held):
            for p in (conf, os.path.join(bng, "accel-ppp.conf"),
                      os.path.join(bng, "chap-secrets"),
                      os.path.join(bng, "access.env")):
                if os.path.exists(p):
                    os.unlink(p)
            return True, "not configured"
        os.makedirs(bng, mode=0o770, exist_ok=True)
        _write(os.path.join(bng, "accel-ppp.conf"), accel_conf(held, acc))
        _write(os.path.join(bng, "chap-secrets"), chap_secrets(held))
        _write(os.path.join(bng, "access.env"), access_env(held, acc))
        _write(conf, "\n".join(nest(held)) + "\n")
    except OSError as e:
        return False, f"could not write {e.filename}: {e.strerror}"
    return True, "written"


def read_conf(path=None):
    """The model pppoe.conf holds; empty() when there is none."""
    held = empty()
    try:
        with open(path or CONF_PATH) as f:
            text = f.read()
    except OSError:
        return held
    for line in text.splitlines():
        s = line.strip()
        if not s or s in (PPPOE_HIERARCHY, "exit") or s.startswith("#"):
            continue
        stage(held, [PPPOE_HIERARCHY] + s.split())
    return held


# --------------------------------------------------------------- operation

#: The columns prisma-bng asks accel-cmd for (`prisma-bng sessions`).
SESSION_COLUMNS = ("sid", "ifname", "username", "ip", "calling-sid", "state",
                   "uptime-raw", "rx-bytes-raw", "tx-bytes-raw")


def session_rows(text):
    """accel-cmd's `show sessions` table as dicts, keyed by SESSION_COLUMNS."""
    rows = []
    for line in text.splitlines():
        if "|" not in line or line.lstrip().startswith("sid "):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) != len(SESSION_COLUMNS):
            continue
        rows.append(dict(zip(SESSION_COLUMNS, cells)))
    return rows


def _uptime(raw):
    """Seconds as d+hh:mm:ss, the way show commands here print a duration."""
    if not is_number(raw):
        return raw or "-"
    s = int(raw)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return (f"{d}d+" if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def sessions_screen(rows):
    """
    `show pppoe sessions`: one line a session, and a count. A session not
    yet active (still authenticating) or finishing says so where its uptime
    would be - a column of its own would not fit a console beside the MAC.
    """
    if not rows:
        return ["no PPPoE session"]
    out = [f"{'session':<16}  {'user':<12} {'address':<15} {'mac':<17} "
           f"{'uptime':>11}"]
    for r in sorted(rows, key=lambda r: (r["username"], r["sid"])):
        out.append(f"{r['sid']:<16}  {r['username'][:12]:<12} "
                   f"{r['ip'] or '-':<15} {r['calling-sid']:<17} "
                   f"{(_uptime(r['uptime-raw']) if r['state'] == 'active' else r['state']):>11}")
    out.append(f"{len(rows)} session(s)")
    return out


def stat_counts(text):
    """`active`, `starting`, `finishing` out of accel-cmd's `show stat`."""
    counts, in_sessions = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s == "sessions:":
            in_sessions = True
            continue
        if in_sessions:
            k, _, v = s.partition(":")
            if v.strip().isdigit():
                counts[k.strip()] = int(v)
            else:
                in_sessions = False
    return counts


def summary_screen(held, status, counts):
    """`show pppoe summary`: the container, the sessions, the configuration."""
    if is_empty(held):
        return ["PPPoE is not configured"]
    word = {"running": "running", "stopped": "configured, NOT running",
            "not-configured": "committed, NOT written",
            "no-containers": "needs the containers; this box runs units"}
    out = [f"{'control plane':<22}{word.get(status, status)}"]
    if status == "running":
        out.append(f"{'sessions':<22}{counts.get('active', 0)} active, "
                   f"{counts.get('starting', 0)} starting, "
                   f"{counts.get('finishing', 0)} finishing")
    out.append(f"{'access interfaces':<22}" + ", ".join(held["interfaces"]))
    if held["radius"]:
        out.append(f"{'authentication':<22}RADIUS: " + ", ".join(
            r["address"] for r in held["radius"]))
    else:
        out.append(f"{'authentication':<22}{len(held['users'])} local "
                   f"user(s), for a lab")
    out.append(f"{'addresses':<22}" + (", ".join(held["pools"])
                                       or "from RADIUS only"))
    out.append(f"{'gateway':<22}{held['gateway']}")
    if "session-limit" in held["settings"]:
        out.append(f"{'session limit':<22}{held['settings']['session-limit']}")
    return out


_SID = re.compile(r"^[0-9a-fA-F]{16}$")


def clear_words(words):
    """
    `clear pppoe session <id> | user <name> | all`'s words after `session`,
    as prisma-bng's `terminate` arguments, and why not.
    """
    if words == ["all"]:
        return ["all"], None
    if len(words) == 1 and _SID.match(words[0]):
        return ["sid", words[0].lower()], None
    if len(words) == 2 and words[0] == "user" and _NAME.match(words[1]):
        return ["username", words[1]], None
    return None, ("clear pppoe session <session-id> | user <name> | all; "
                  "the id is the 16-hex session id 'show pppoe sessions' "
                  "lists, the Acct-Session-Id RADIUS was sent")


def cp_taps(show_tap):
    """
    The VPP names of the TAPs whose Linux end is the control-plane interface,
    out of `show tap`: `Interface: tap1863 (ifindex 2)` then `name
    "pppoe-cp"` a few rows below it.
    """
    out, cur = set(), None
    for line in show_tap.splitlines():
        m = re.match(r"^Interface:\s+(\S+)", line)
        if m:
            cur = m.group(1)
            continue
        if cur and line.strip() == f'name "{CP_LINUX}"':
            out.add(cur)
    return out


def interface_names(show_interface):
    """
    The interfaces `show interface` lists: the first word of each row that
    starts at the left edge, the header's excepted. device_mtus() is not
    this: it leaves out a sub-interface, whose MTU VPP reports as 0.
    """
    return {line.split()[0] for line in show_interface.splitlines()
            if line.strip() and not line[0].isspace()
            and line.split()[0] != "Name"}
