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

RADIUS IS NOT HERE since #607: it is `aaa` > `radius` (aaa.py), which this
module is handed wherever accel-ppp's file needs it. A 0.154.0 file's `pppoe
radius ...` statements are read into that block (config.py), and refused at
the prompt with where they went.

SECRETS. A local user's password is a password on the wire, held like SNMP's
community: in the saved configuration and in pppoe.conf (0660, group
prisma-cgnat, as prisma-routing.conf is), never on a screen - `redact()` is
what every show and compare passes the text through.

RATE LIMITS (#607). accel-ppp-ng's shaper module, given a session with no
kernel interface, hands the limit to the session hooks - and its vyos_vpp
hooks install a VPP policer on the session's own interface, the download one
on output and the upload one on input (session_hooks/vyos_vpp/vpppolicer.c).
So the module is loaded, told which RADIUS attribute carries the rate, and a
CoA carrying a new one changes the policers on the live session. The rate is
Prisma's own pair of attributes, vendor 67033 (appliance/dictionary.prisma):
Prisma-Rate-Limit-Down and -Up, integers in kbit/s - by the operator's
decision the primary form; Cisco-AVPair's rate-limit and sub-qos-policy
forms are read too (build/prisma_rate.c), and Prisma's wins when both are
there. MikroTik's is not read.
"""
import ipaddress
import os
import re

import aaa as aaa_mod
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
#: The session trail (docs/bng.md 0.7): who held which inside address from
#: when to when, written by the control plane's prisma_trail module on the
#: log volume beside the configuration trail - root's directory, read through
#: the host's door (pppoetrail.py). Not under LOG_DIR: accel-ppp's own logs
#: are a diagnosis, this is a record kept like config-history.log.
TRAIL_PATH = "/var/log/prisma-routing/pppoe-sessions.log"
#: The RADIUS dictionary the image installs.
DICTIONARY = "/usr/share/accel-ppp/radius/dictionary"

AUTH_METHODS = ("pap", "chap", "mschap-v2")
#: accel-ppp's module for each.
AUTH_MODULE = {"pap": "auth_pap", "chap": "auth_chap_md5",
               "mschap-v2": "auth_mschap_v2"}

#: Settings that take one value, with the bounds each is checked against.
#: The defaults are what an absent statement means; they are never written.
DEFAULTS = {"mtu": "1492", "authentication": "pap chap mschap-v2",
            "ac-name": "prisma-routing"}
DNS_MAX = 2
POOLS_MAX = 16
INTERFACES_MAX = 16
USERS_MAX = 1000
SESSION_LIMIT_MAX = 1000000
MTU_MIN, MTU_MAX = 576, 1500
MSS_MIN, MSS_MAX = 536, 1460

#: Where a session's rate comes from, in an Access-Accept or a CoA-Request:
#: Prisma's vendor-specific attributes (#607), the shaper's `vendor`,
#: `attr-down` and `attr-up`. Integers in kbit/s, so nothing is parsed.
RATE_VENDOR = "Prisma"
RATE_DOWN, RATE_UP = "Prisma-Rate-Limit-Down", "Prisma-Rate-Limit-Up"
#: A session policer's bucket, in seconds of its rate (0.155.2; shaper_section
#: has the measurements). Both sources, both directions.
BURST_SECONDS = "0.5"
#: Where the image puts the dictionary that names them (build/Dockerfile.bng).
PRISMA_DICTIONARY = "/usr/share/prisma-bng/dictionary.prisma"
#: The fastest a session's policer takes: past it, accel-ppp's bytes per
#: second overflow an int (17 Gbit/s; build/accel-ppp-ng.patch moved the
#: earlier overflow, 2.1 Gbit/s, to 64 bits).
RATE_MAX_KBIT = 10000000
_RATE = re.compile(r"^([0-9]{1,8})([KMG]?)$", re.IGNORECASE)

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
            "settings": {}, "users": {}}


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


def rate_kbit(word):
    """A rate - kbit/s, or with K, M or G - as kbit/s, or None."""
    m = _RATE.match(word or "")
    if not m:
        return None
    n = int(m.group(1)) * {"": 1, "K": 1, "M": 1000,
                           "G": 1000000}[m.group(2).upper()]
    return n if 0 < n <= RATE_MAX_KBIT else None


def rate_words(kbit):
    """kbit/s as the shortest of 20M, 1500K, 1G."""
    for unit, n in (("G", 1000000), ("M", 1000)):
        if kbit and kbit % n == 0:
            return f"{kbit // n}{unit}"
    return f"{kbit}K"


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
    elif key == "rate-limit-default":
        down, _, up = value.partition("/")
        if rate_kbit(down) is None or rate_kbit(up) is None:
            return ("rate-limit default <download>/<upload>: kbit/s, or "
                    "with K, M or G, as 20M/10M; at most 10G")
    elif key == "ac-name":
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
STATEMENTS = ("interface", "gateway", "address-pool", "dns",
              "authentication", "session-limit", "mtu", "tcp-mss", "ac-name",
              "rate-limit", "local-user")
SAYS = ("under 'pppoe': interface, gateway, address-pool, dns, "
        "authentication, session-limit, mtu, tcp-mss, ac-name, rate-limit, "
        "local-user")


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
        # #607: one spelling. A saved file's are read into `aaa radius`
        # before they get here (config.py); typed, they are told where.
        return aaa_mod.MOVED_SAYS
    if kw == "rate-limit":
        if args[:1] != ["default"]:
            return ("rate-limit default <download>/<upload>; a session's own "
                    "comes from RADIUS, Prisma-Rate-Limit-Down and -Up")
        key = "rate-limit-default"
        if neg:
            if len(args) != 1:
                return "no rate-limit default"
            s.pop(key, None)
            return None
        if len(args) != 2:
            return "rate-limit default <download>/<upload>"
        why = setting_error(key, args[1])
        if why:
            return why
        s[key] = args[1]
        return None
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
    s = held["settings"]
    for k in ("authentication", "session-limit", "mtu", "tcp-mss", "ac-name"):
        if k in s:
            out.append(f"{k} {s[k]}")
    if "rate-limit-default" in s:
        out.append(f"rate-limit default {s['rate-limit-default']}")
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
    """
    A local user's password, never on a screen - and a 0.154.0 `radius
    server` line's secret, which a rollback revision or a compare against
    one still carries.
    """
    out = []
    for line in lines:
        w = line.split()
        pre = line[:len(line) - len(line.lstrip())]
        at = 1 if w[:1] == [PPPOE_HIERARCHY] else 0
        tail = w[at:]
        if tail[:2] == ["radius", "server"] and "secret" in tail \
                and " ".join(w[w.index("secret") + 1:][:3]) != NOT_SHOWN:
            i = w.index("secret")
            out.append(pre + " ".join(w[:i + 1] + [NOT_SHOWN] + w[i + 2:]))
        elif tail[:1] == ["local-user"] and "password" in tail \
                and " ".join(w[w.index("password") + 1:][:3]) != NOT_SHOWN:
            i = w.index("password")
            out.append(pre + " ".join(w[:i + 1] + [NOT_SHOWN] + w[i + 2:]))
        else:
            out.append(line)
    return out


def setting(held, key):
    """A single-valued setting, or its default."""
    return held["settings"].get(key, DEFAULTS.get(key, ""))


def uses_radius(aaa):
    """
    True when PPPoE authenticates against RADIUS: whenever `aaa radius` has
    a server. accel-ppp's radius module answers every Access-Request it is
    loaded for, so a local user beside it would be asked of RADIUS too.
    """
    return bool(aaa["servers"])


def check(held, aaa=None):
    """
    What makes a configuration one the container cannot run, as rows; empty
    when it can. Asked at commit, where the operator is: a container started
    on a configuration accel-ppp cannot use is a box that answers no PADI and
    says nothing. `aaa` is the `aaa` block, where its RADIUS is.
    """
    aaa = aaa if aaa is not None else aaa_mod.empty()
    if is_empty(held):
        return []
    why = []
    if not held["interfaces"]:
        why.append("pppoe: no access interface ('interface <name>')")
    if not held["gateway"]:
        why.append("pppoe: no gateway ('gateway <address>'), the address the "
                   "subscribers see the box at")
    if not uses_radius(aaa) and not held["users"]:
        why.append("pppoe: nobody to authenticate against: a server under "
                   "'aaa radius' or, for a lab, a local-user")
    if not held["pools"] and not uses_radius(aaa):
        why.append("pppoe: no address-pool, and no RADIUS under 'aaa "
                   "radius' to give a Framed-IP-Address")
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


#: What generated a file, and with it what is in it: a box whose file says
#: otherwise was generated by an older release, and its files are written
#: again at the next load (prismash.Shell.apply_pppoe). 2: #607's [radius]
#: and [shaper]. 3: half a second of burst (0.155.2) - a box upgraded from
#: 0.155.1 gets the new buckets at its next load, not at its next commit.
#: 4: the session trail's module and its [prisma-trail] (docs/bng.md 0.7) - a
#: box upgraded with PPPoE running starts recording at its next load.
#: 5: access.env names the access interfaces (ACCESS), which the entrypoint
#: hands the dataplane's PPPoE guard (#597, docs/bng.md 0.3 item 9).
FORMAT = "5"
FORMAT_LINE = f"# prisma-bng format {FORMAT}"


def shaper_section(held, aaa=None):
    """
    accel-ppp.conf's [shaper]: the attributes that carry the rate, the VPP
    policer it becomes, and the default for a session with none. Two
    attributes, one a direction: the shaper reads an integer as kbit/s
    (shaper.c parse_attr), so nothing is parsed out of a string.

      vpp-policer-type=1r2c  one rate, one bucket: up to the rate passes,
                             past it is dropped. accel-ppp-ng's default is
                             2r3c with the committed rate at 0.8 of it,
                             which measures as the rate anyway and reads as
                             a policer half of whose numbers are not the
                             subscriber's plan;
      down-, up-burst-factor=0.5  half a second at the rate, each way,
                             in bytes (the patched hook keeps a 15,000-byte
                             floor). A tenth of a second (0.155.1) halved a
                             single TCP flow on prisma-bench, 7 workers:
                             50 Mbit/s measured 25.5/24.9 down over 6/20 s,
                             20/10 measured 9.8/4.7 - what a speedtest shows
                             - while UDP passed exactly (49.1). The bench,
                             changing the live policer's cb: 0.25 s 48.6,
                             0.5 s 50.5, 1.0 s 54.3, 1.5 s 58.5 over 6 s;
                             10/5 at 0.5 s 10.1/5.07. Half a second is the
                             smallest that gives one flow the plan without
                             a short test reading well over it.
    """
    out = ["[shaper]", f"vendor={RATE_VENDOR}", f"attr-down={RATE_DOWN}",
           f"attr-up={RATE_UP}", "vpp-policer-type=1r2c",
           f"down-burst-factor={BURST_SECONDS}",
           f"up-burst-factor={BURST_SECONDS}", "verbose=1"]
    dflt = held["settings"].get("rate-limit-default")
    if dflt:
        down, _, up = dflt.partition("/")
        out.append(f"rate-limit={rate_kbit(down)}/{rate_kbit(up)}")
    # The names a Cisco-AVPair sub-qos-policy may give (#607).
    out += aaa_mod.shaper_profiles(aaa if aaa is not None
                                   else aaa_mod.empty())
    return out


def modules(held, aaa):
    """
    accel-ppp's [modules], which it reads at START only: a change here is a
    restart, never a reload (prismash.Shell.apply_pppoe).
    """
    auth = setting(held, "authentication").split()
    mods = ["log_file", "vyos_vpp", "pppoe"] + [AUTH_MODULE[a] for a in auth]
    mods.append("radius" if uses_radius(aaa) else "chap-secrets")
    if held["pools"]:
        mods.append("ippool")
    # After radius, whose Access-Accept and CoA events it takes.
    mods.append("shaper")
    mods.append("pppd_compat")
    # The session trail (docs/bng.md 0.7). Its events are the session's own,
    # not RADIUS's, so where it is in the list changes nothing it records.
    mods.append("prisma_trail")
    return mods


def trail_section(acc):
    """
    accel-ppp.conf's [prisma-trail]: where the session trail is written, and
    each access interface's Linux name (what accel-ppp knows a session came
    in on) as the name the VTY gives it - so the record says `ens19.100`,
    which an operator configured, and not `pppoe-cp.100`, which nobody did.
    """
    out = ["[prisma-trail]", f"path={TRAIL_PATH}"]
    out += [f"interface-{cp_name(v)}={name}" for name, _, v in acc]
    return out


def accel_conf(held, acc, aaa=None):
    """
    accel-ppp.conf, generated. `acc` is access()'s list, `aaa` the `aaa`
    block. The CLI section is not here: the entrypoint adds it with a
    password made at each start.
    """
    aaa = aaa if aaa is not None else aaa_mod.empty()
    mods = modules(held, aaa)
    mtu = setting(held, "mtu")
    out = ["# GENERATED by prismash from the committed configuration (#597).",
           "# Do not edit: the next commit rewrites it, and 'show",
           "# running-config' never reads it.", FORMAT_LINE,
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
    if uses_radius(aaa):
        out += [""] + aaa_mod.accel_radius(aaa, held["gateway"])
    else:
        out += ["", "[chap-secrets]", f"gw-ip-address={held['gateway']}",
                f"chap-secrets={BNG_DIR}/chap-secrets"]
    if held["pools"]:
        out += ["", "[ip-pool]", f"gw-ip-address={held['gateway']}"]
        out += held["pools"]
    out += [""] + shaper_section(held, aaa)
    out += [""] + trail_section(acc)
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
             # The dataplane's own names, which the PPPoE guard is given:
             # the only interfaces PPPoE is taken from (format 5).
             "ACCESS=\"" + " ".join(n for n, _, _ in acc) + "\"",
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


def file_format(bng=None):
    """The FORMAT the container's accel-ppp.conf was generated in, or ""."""
    try:
        with open(os.path.join(bng or BNG_DIR, "accel-ppp.conf")) as f:
            for line in f:
                if line.startswith("# prisma-bng format "):
                    return line.split()[-1]
    except OSError:
        pass
    return ""


def write_files(held, acc, conf=None, bng=None, aaa=None):
    """
    The model into pppoe.conf and, when it is set, the container's files into
    BNG_DIR; with nothing set, both removed. (True, what) or (False, why).
    `aaa` is the `aaa` block the [radius] section is written from.
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
        _write(os.path.join(bng, "accel-ppp.conf"),
               accel_conf(held, acc, aaa))
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
                   "uptime-raw", "rx-bytes-raw", "tx-bytes-raw", "rate-limit")


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


def limit_words(raw):
    """
    The shaper's `rate-limit` column - "<down>/<up>" in kbit/s, empty for
    none - as "20M/10M", or "-".
    """
    down, _, up = (raw or "").partition("/")
    if not is_number(down) or not is_number(up) or not (int(down)
                                                         or int(up)):
        return "-"
    return "/".join(rate_words(int(x)) if int(x) else "none"
                    for x in (down, up))


def sessions_screen(rows):
    """
    `show pppoe sessions`: one line a session, and a count. A session not
    yet active (still authenticating) or finishing says so where its uptime
    would be. The limit is download/upload; the MAC moved to `show pppoe
    session <id>` (#607) - both do not fit a console beside the address.
    """
    if not rows:
        return ["no PPPoE session"]
    out = [f"{'session':<16}  {'user':<12} {'address':<15} "
           f"{'limit down/up':<15} {'uptime':>11}"]
    for r in sorted(rows, key=lambda r: (r["username"], r["sid"])):
        out.append(f"{r['sid']:<16}  {r['username'][:12]:<12} "
                   f"{r['ip'] or '-':<15} "
                   f"{limit_words(r.get('rate-limit')):<15} "
                   f"{(_uptime(r['uptime-raw']) if r['state'] == 'active' else r['state']):>11}")
    out.append(f"{len(rows)} session(s)")
    return out


def policers(show_policer):
    """
    {name: {"conform": packets, "dropped": packets}} out of VPP's `show
    policer`: a block a policer, `Name "<name>" ...` then `conform <n>
    packets, ...`, `exceed ...`, `violate ...`. With accel-ppp's 1r2c, past
    the rate is `exceed`, and dropped; `violate` is counted in for a policer
    of another type.
    """
    out, cur = {}, None
    for line in show_policer.splitlines():
        m = re.match(r'^Name "([^"]+)"', line.strip())
        if m:
            cur = out.setdefault(m.group(1), {"conform": 0, "dropped": 0})
            continue
        m = re.match(r"^(conform|exceed|violate) (\d+) packets", line.strip())
        if cur is not None and m:
            key = "conform" if m.group(1) == "conform" else "dropped"
            cur[key] += int(m.group(2))
    return out


def session_index(show_session, mac):
    """
    The sw_if_index of the VPP session whose client is `mac`, or "". By the
    MAC and not the address: VPP's own session table holds client-ip 0.0.0.0
    for every accel-ppp session (docs/bng.md 0.3, item 1), measured in
    lab/pppoe.sh. A block is `[n] sw-if-index <i> client-ip ...` then
    `local-mac ...  client-mac <mac>`.
    """
    idx = ""
    for line in show_session.splitlines():
        m = re.search(r"sw-if-index (\d+)", line)
        if m:
            idx = m.group(1)
        if mac and f"client-mac {mac.lower()}" in line.lower() and idx:
            return idx
    return ""


def session_screen(row, sw_if_index="", pols=None):
    """
    `show pppoe session <id>`: one session whole - what the list has no
    room for, and its policers as VPP counts them. accel-ppp names a
    session's policers vyos_<sw_if_index>_<kbit>_<burst>_down|up
    (session_hooks/vyos_vpp/vpppolicer.c), which is how they are found.
    """
    pols = pols or {}
    out = [f"{'session':<14}{row['sid']}",
           f"{'user':<14}{row['username']}",
           f"{'address':<14}{row['ip'] or '-'}",
           f"{'mac':<14}{row['calling-sid'] or '-'}",
           f"{'state':<14}{row['state']}",
           f"{'uptime':<14}{_uptime(row['uptime-raw'])}",
           f"{'traffic':<14}{row['rx-bytes-raw'] or 0} bytes from the "
           f"subscriber, {row['tx-bytes-raw'] or 0} to it",
           f"{'limit':<14}" + (lambda w: "none" if w == "-" else
                               w.replace("/", " down, ", 1) + " up")(
               limit_words(row.get("rate-limit")))]
    for d, word in (("down", "download"), ("up", "upload")):
        mine = [n for n in pols if sw_if_index
                and n.startswith(f"vyos_{sw_if_index}_")
                and n.endswith(f"_{d}")]
        for n in mine:
            out.append(f"{'policer ' + d:<14}{pols[n]['conform']} packets "
                       f"passed, {pols[n]['dropped']} dropped ({word})")
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
            if is_number(v.strip()):
                counts[k.strip()] = int(v)
            else:
                in_sessions = False
    return counts


def trail_counts(text):
    """
    The session trail's block of accel-cmd's `show stat` (docs/bng.md 0.7):
    {"written", "failed", "seq"}, or {} when the control plane has no trail
    module - a container older than the trail.
    """
    counts, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s == "prisma-trail:":
            inside = True
            continue
        if inside:
            k, _, v = s.partition(":")
            if k.strip() in ("written", "failed", "seq") and is_number(v.strip()):
                counts[k.strip()] = int(v)
            else:
                inside = False
    return counts




_GUARD_ROW = re.compile(r"^(discovery|session) passed (\d+) not-access "
                        r"(\d+) held (\d+)$")


def guard_counts(text):
    """
    The dataplane's PPPoE guard (#597, the plugin's `show bpa
    pppoe-access`) as {"not-access": n, "held": n}, discovery and session
    frames together; None when the guard is not set or the plugin is older
    than it.
    """
    out = {"not-access": 0, "held": 0}
    seen = False
    for line in (text or "").splitlines():
        m = _GUARD_ROW.match(line.strip())
        if m:
            seen = True
            out["not-access"] += int(m.group(3))
            out["held"] += int(m.group(4))
    return out if seen else None


def summary_screen(held, status, counts, aaa=None, states=None, trail=None,
                   guard=None):
    """
    `show pppoe summary`: the container, the sessions, the configuration -
    and, with RADIUS, each server and whether accel-ppp has it marked down
    (`states`, aaa.server_states()); with `guard` (guard_counts()), the
    PPPoE frames the dataplane refused before they reached the control
    plane.
    """
    aaa = aaa if aaa is not None else aaa_mod.empty()
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
        # The session trail (docs/bng.md 0.7): an entry it could not write
        # is a session the box cannot answer for, and it says so here.
        if trail:
            row = f"{trail.get('written', 0)} entries written"
            if trail.get("failed"):
                row += f", {trail['failed']} NOT written"
            out.append(f"{'session trail':<22}{row}")
        elif trail is not None:
            out.append(f"{'session trail':<22}NOT recorded: the control "
                       f"plane has no trail")
    out.append(f"{'access interfaces':<22}" + ", ".join(held["interfaces"]))
    if guard is not None:
        # Two rows, each at most 79 columns with a 20-digit count.
        out.append(f"{'refused':<22}{guard['not-access']} from outside "
                   f"the access interfaces")
        out.append(f"{'':<22}{guard['held']} for a MAC another access "
                   f"port holds")
    if uses_radius(aaa):
        out.append(f"{'authentication':<22}RADIUS, 'aaa radius'")
        out += aaa_mod.summary_rows(aaa, states)
    else:
        out.append(f"{'authentication':<22}{len(held['users'])} local "
                   f"user(s), for a lab")
    out.append(f"{'addresses':<22}" + (", ".join(held["pools"])
                                       or "from RADIUS only"))
    out.append(f"{'gateway':<22}{held['gateway']}")
    if "session-limit" in held["settings"]:
        out.append(f"{'session limit':<22}{held['settings']['session-limit']}")
    dflt = held["settings"].get("rate-limit-default")
    out.append(f"{'rate limit':<22}from RADIUS (Prisma-Rate-Limit-*), else "
               + (limit_words("/".join(str(rate_kbit(x))
                                       for x in dflt.split("/")))
                  if dflt else "none"))
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
