#!/usr/bin/env python3
"""
The configuration that lives on the box rather than in VPP: the SSH daemon and
the accounts that may log in.

Everything here is a system object, not a private database. Accounts are real
POSIX accounts and keys are real `authorized_keys` files, because an appliance
that invents its own accounts is an appliance whose accounts nobody
deprovisions when somebody leaves.

Reading is done from the system itself - `sshd -T` for the effective daemon
configuration, `/etc/passwd` for the accounts - for the same reason the CGNAT
configuration is read back from the dataplane: a VTY that reports what it
believes it wrote is a VTY that cannot notice being wrong.
"""

from __future__ import annotations

import errno
import ipaddress
import os
import shutil
import subprocess

SSHD_DROPIN = "/etc/ssh/sshd_config.d/prisma-routing.conf"

# The resolvers (#437). The image has no resolvconf and no systemd-resolved;
# a static management answer has the installer write /etc/resolv.conf once,
# and a dhcp one has dhclient-script rewrite it on every lease. So the VTY
# writes the file itself, says so on its FIRST LINE, and the DHCP hook
# appliance/dhclient-prisma-routing-dns stands down make_resolv_conf() for as
# long as that line is there. What was in the file before the VTY took it
# over is kept, and put back when the last `dns server` is removed.
RESOLV_CONF = "/etc/resolv.conf"
RESOLV_BEFORE = "/etc/prisma-routing/resolv.conf.before"
DNS_OWNED = "# Written by Prisma Routing: 'dns server' in the VTY owns this file."
#: glibc reads three `nameserver` lines (MAXNS) and ignores the rest, so a
#: fourth would be accepted, shown, and never asked.
DNS_MAX = 3
LOGIN_SHELL = "/usr/bin/prismash-login"

# THE BOX'S NAME (#516): Linux's, FRR's and SNMP's sysName, set in one place.
# The default is the installer's own ("prisma-routing", #202), which is what
# `no hostname` goes back to.
HOSTNAME_DEFAULT = "prisma-routing"
#: HOST_NAME_MAX: the kernel keeps 64 characters and cuts the rest.
HOSTNAME_MAX = 64
#: The host program that renames the box - /etc/hostname, the 127.0.1.1 line
#: of /etc/hosts and the kernel's name - behind the door of the same shape.
SET_HOSTNAME = "/usr/lib/prisma-routing/set-hostname"

# Order matters: it is the order these are written out in, and a stable order
# is what keeps a candidate built from the running configuration identical to
# it when nothing changed.
SSH_FIELDS = ("port", "listen-address", "permit-root-login",
              "password-authentication")

# What sshd does when nothing says otherwise. Used only when the daemon cannot
# be asked; they are Debian's documented defaults, not a guess.
SSH_DEFAULTS = {
    "port": "22",
    "listen-address": "0.0.0.0",
    "permit-root-login": "prohibit-password",
    "password-authentication": "yes",
}

# sshd -T prints lowercase keyword names with no separator.
_SSHD_T = {
    "port": "port",
    "listen-address": "listenaddress",
    "permit-root-login": "permitrootlogin",
    "password-authentication": "passwordauthentication",
}

_VALID = {
    "permit-root-login": ("yes", "no", "prohibit-password", "forced-commands-only"),
    "password-authentication": ("yes", "no"),
}

# THE SAME SETTING UNDER TWO NAMES, and the daemon answers with the one this
# VTY does not offer (#304).
#
# OpenSSH accepts `prohibit-password` and `without-password` for
# PermitRootLogin. They mean the same thing; `without-password` is the older
# spelling, kept for compatibility. `sshd -T`, which is how this file reads the
# effective configuration, canonicalises to the OLD one - measured on OpenSSH
# 9.2: a config saying `prohibit-password` reads back as
# `permitrootlogin without-password`.
#
# So the VTY offered `prohibit-password`, wrote it, read back a word it had
# never heard of, and refused its own setting. The box then fails to load its
# saved configuration at boot, which is a box that comes up unconfigured.
#
# Normalised HERE, on the way in, rather than widened in _VALID: two names for
# one setting inside the product would reach `show running-config` and the
# saved file, and an operator comparing two boxes would see a difference that
# is not one. One spelling lives in this product - the one the VTY offers -
# and this is the single place the daemon's other name is translated.
_SSHD_T_ALIASES = {
    "permit-root-login": {"without-password": "prohibit-password"},
}

# ------------------------------------------------------------------- roles
#
# The role an operator chooses, and the POSIX groups it is made of. Groups
# rather than a database of this appliance's own: an appliance that invents
# its own accounts is an appliance whose accounts nobody deprovisions.
ROLE_ADMIN = "administrator"
ROLE_CGNAT = "cgnat-operator"
ROLES = (ROLE_ADMIN, ROLE_CGNAT)

GROUP_ADMIN = "prisma-admin"
GROUP_CGNAT = "prisma-cgnat"
# Not a role: a capability BESIDE whichever role the account has (#563). The
# role answers what an account may CHANGE in the configuration; this answers
# whether it may take the box itself down or move it to another release -
# `system upgrade`, `system reboot`, `system shutdown`, `system dataplane
# restart` - which until now only root could. Orthogonal on purpose: a NOC
# account that may only read the configuration can still be the one that
# reboots the box, and an administrator is not one by being an administrator.
# A group for the same reason the roles are groups: the system's own
# deprovisioning takes it away.
GROUP_OPERATOR = "prisma-operator"

# Not a role and not the operator's to choose: this is the reach an account
# needs before authorization is even the question, and EVERY account this VTY
# owns gets it - a read-only account that cannot open these has no reading to
# do either.
#
#   vpp      VPP creates its CLI and API sockets srwxrwxr-x root:vpp, so the
#            connect bit belongs to the group. Outside it every command fails
#            with permission denied, the read-only ones included.
#   frr      /etc/frr is drwxr-x--- frr:frr and vtysh.conf is 0640 frr:frr.
#   frrvty   the daemons' .vty sockets, which is what vtysh talks to.
#
# So an account granted a role and left out of these has a role it cannot
# exercise, which is the state every account this VTY created used to be in.
REACH_GROUPS = ("vpp", "frr", "frrvty")

# prisma-admin is ADDITIVE on top of prisma-cgnat rather than a superset by name:
# /etc/prisma-routing and the rollback store under it are group prisma-cgnat mode
# 0770 (appliance/install.sh), and an administrator that is not in that group
# cannot record the rollback revision for the change it just made.
ROLE_GROUPS = {
    ROLE_ADMIN: (GROUP_ADMIN, GROUP_CGNAT),
    ROLE_CGNAT: (GROUP_CGNAT,),
}

# The only memberships this VTY adds and removes. An operator may have put an
# account in "sudo" or in a monitoring group for reasons of their own, and a
# demotion that silently took those away would be this VTY deciding something
# nobody asked it to decide.
MANAGED_GROUPS = (GROUP_ADMIN, GROUP_CGNAT, GROUP_OPERATOR) + REACH_GROUPS


#: The one groupadd this VTY runs (#563), on the host through a door.
OPERATOR_GROUPADD = ("groupadd", "--system", GROUP_OPERATOR)


def groups_for(role, operator=False) -> set:
    """The memberships an account with this role (and, when `operator`, the
    system-actions capability, #563) must have."""
    return set(REACH_GROUPS) | set(ROLE_GROUPS.get(role, ())) | \
        ({GROUP_OPERATOR} if operator else set())


def role_from_groups(names) -> str:
    """
    The role a set of group names amounts to, or "" for read-only.

    Deliberately the same two questions, in the same order, that
    prismash.capabilities() asks of the running process - a running
    configuration that named a role the VTY would not grant would be a report
    of something that is not true.
    """
    if GROUP_ADMIN in names:
        return ROLE_ADMIN
    if GROUP_CGNAT in names:
        return ROLE_CGNAT
    return ""


def is_number(v) -> bool:
    """
    True if `v` is a decimal number THIS BOX WILL PARSE, ASCII digits only.

    WHY NOT `str.isdigit()`, which is what every one of these call sites used
    until #412: it is true for characters `int()` refuses, so the guard and the
    conversion disagree and the disagreement is a traceback. Measured:

        '\u00b2'  isdigit=True   int() -> ValueError
        '\u2467'  isdigit=True   int() -> ValueError
        '\u0663'  isdigit=True   int() -> 3
        '\U0001d7da'  isdigit=True   int() -> 2

    The first two ended the VTY session and ended `prismash --load` at boot,
    at every numeric statement this product has, through both doors. The last
    two are the quieter half: they convert, so a saved file carrying one comes
    back a DIFFERENT file - `save` writes the ASCII digit - and a
    configuration's text stops round-tripping.

    `isdecimal()` alone fixes neither: it is true for both of the last two.
    ASCII is the rule because ASCII is what `int()` takes, what the dataplane
    prints with `%u`, and what an operator can type back in.

    IT LIVES HERE, in the lowest module, because both parsers need it and
    `config` already imports this one. One definition is the entire point: the
    defect was one wrong predicate repeated forty-one times, and a second
    definition would be that defect starting again.
    """
    v = str(v)
    return v.isascii() and v.isdigit()


def address_error(what: str, value: str):
    """
    None if `value` is an IP address, otherwise why it is not.

    An ADDRESS and not a prefix: this is for the places a single host is
    named - a collector, a source, an address a daemon listens on - where a
    prefix length is not part of the grammar and would be as wrong as a word.

    `ipaddress` and not a hand-written dotted quad, because the questions it
    already answers are the ones that get forgotten: `010.1.1.1` is octal to
    some resolvers and decimal to others, and CPython refuses it for that
    reason. `config._is_ipv4_address` refuses it too and says so; this is the
    same rule for both families, from the library rather than from a second
    copy.

    IT LIVES HERE for the reason `is_number` does: `validate_ssh` and
    `validate_snmp` are in this module and `config` imports it, not the other
    way round. One definition, and the two doors read it.
    """
    if not value:
        return f"{what}: expected an address"
    try:
        ipaddress.ip_address(unbracket(value))
    except ValueError:
        if "/" in value:
            return (f"{what} '{value}': expected an address, not a prefix")
        return f"{what} '{value}': not an IP address"
    return None


def unbracket(value: str) -> str:
    """
    An IPv6 literal without its brackets, and anything else unchanged.

    `[::]` IS AN ADDRESS HERE, and this is not a courtesy to a spelling. It is
    what `sshd -T` prints for an IPv6 ListenAddress and therefore what
    `read_ssh` stores, so it is the value on every installed box - and
    `address_error` refusing it made **every commit on an installed box fail**:

        error: ssh listen-address '[::]': not an IP address
        error: candidate did not pass validation; nothing applied

    Measured by tools/release-gate.sh on v0.110.0, where it took the console
    driver's first commit and with it seven later checks that never ran. Not
    reachable from `make test`, which installs nothing and never asks sshd
    what it is doing - the gate is the only thing that could have caught it,
    and did.

    The general shape is #357's and it is worth naming: a validator placed on
    a value the box also READS BACK has to accept every form the box itself
    writes, or the box stops being able to agree with its own configuration.
    """
    if len(value) > 1 and value[0] == "[" and value[-1] == "]":
        return value[1:-1]
    return value


def ssh_listen_normal(value: str) -> str:
    """
    A ListenAddress in the spelling `sshd -T` gives back.

    IPv6 bracketed, IPv4 bare. Normalised for the reason `config.mac_normal`
    exists (#414): the candidate is compared against what the box reports, so
    a value stored in the other spelling is a difference on every commit -
    the box re-applying a setting forever and `show configuration compare`
    never clean. Typing `::` and reading back `[::]` was exactly that, and it
    predates the refusal above rather than arriving with it.
    """
    bare = unbracket(value)
    try:
        addr = ipaddress.ip_address(bare)
    except ValueError:
        return value
    return f"[{bare}]" if addr.version == 6 else bare


def validate_role(name, role):
    """
    Why this account cannot be given this role, or None.

    A TUPLE of rows, always, even where there is one: the caller at the prompt
    prints the first through `err` and the rest as prose, because as one
    sentence the root refusal reaches the console at 104 columns (#355). The
    caller in the configuration parser joins them, because an entry in
    `unknown` is one line of prose by construction.
    """
    if name == "root":
        # root has every capability by being root - capabilities() returns
        # both for euid 0 and never looks at a group. Writing a role here
        # would be a line the box could not read back, so every commit
        # afterwards would see a difference and try to apply it again.
        # A pair, and the caller prints the second row as prose: as one
        # sentence it reaches the console at 104 columns (#355).
        return ("root's rights come from being root, not from a group",
                "this VTY does not manage root's memberships")
    if role and role not in ROLES:
        return (f"role '{role}': expected one of " + ", ".join(ROLES),)
    return None


def validate_operator(name):
    """
    Why this account cannot be given the system actions (#563), or None: a
    tuple of rows, as validate_role() gives. root holds them by being root.
    """
    if name == "root":
        return ("root may already upgrade, reboot and shut the box down",
                "'operator' is for accounts that are not root")
    return None


class User:
    def __init__(self, name):
        self.name = name
        self.keys: list[str] = []
        self.password = ""      # crypt(3) hash, or "" for none
        self.role = ""          # "" is read-only: no role group at all
        self.operator = False   # the system actions, beside the role (#563)

    def lines(self) -> list[str]:
        out = [f"user {self.name}"]
        if self.role:
            out.append(f" role {self.role}")
        if self.operator:
            out.append(" operator")
        out += [f" ssh-key {k}" for k in self.keys]
        if self.password:
            out.append(f" password {self.password}")
        out.append(" exit")
        return out

    def __eq__(self, other):
        return isinstance(other, User) and self.lines() == other.lines()


def redact(lines):
    """
    The same configuration with password hashes replaced.

    A hash on a screen shared during an incident is a hash that has left the
    building, and it is offline-crackable. This is a display transform only:
    the stored configuration and every rollback revision keep the real value,
    or a rollback could not restore a password.
    """
    out = []
    for l in lines:
        t = l.strip()
        if t.startswith("password "):
            out.append(" password <set, not shown>")
            continue
        # `neighbor <address> password <secret>`. Worse than an account's
        # entry, which is a hash: this is the secret itself, and it opens the
        # session on the OTHER router too. The account form above does not
        # match it - the line does not start with "password" - so it was
        # printed in the clear by `show running-config` and by `show this`.
        w = t.split()
        if len(w) >= 4 and w[0] == "neighbor" and w[2] == "password":
            indent = l[:len(l) - len(l.lstrip())]
            out.append(f"{indent}neighbor {w[1]} password <set, not shown>")
            continue
        # The IGPs authenticate too, and none of their five spellings starts
        # with "password" or looks like the BGP one, so every OSPF and IS-IS
        # key printed in full (#169). They are secrets, not hashes, and like a
        # neighbour's each one opens the adjacency on the OTHER router.
        #
        # Where the secret sits is spelled out per form. Redacting any word
        # after "password" would take `neighbor PG peer-group` with it, and
        # redacting any line containing the word would take a description that
        # merely mentions one - a configuration mangled on screen is a
        # configuration an operator retypes wrong.
        secret = None
        if w[:3] == ["ip", "ospf", "authentication-key"] and len(w) >= 4:
            secret = 3
        elif (w[:3] == ["ip", "ospf", "message-digest-key"]
              and len(w) >= 6 and w[4] == "md5"):
            secret = 5
        elif w[:2] == ["isis", "password"] and len(w) >= 4:
            secret = 3
        elif w and w[0] in ("domain-password", "area-password") and len(w) >= 3:
            secret = 2
        # A key chain is the OTHER way OSPF, IS-IS and RIP carry a secret, and
        # none of the five spellings above matches it, so it printed in full
        # (#181). `key-string` is the secret itself; `key chain KC-CORE` and
        # `key 1` above it are a name and an id, and redacting those would
        # leave an operator unable to tell WHICH chain is on screen.
        elif w[:1] == ["key-string"] and len(w) >= 2:
            secret = 1
        # RIP's own inline form. `ip rip authentication key-chain KC-CORE`
        # names a chain and must survive; only `string` carries the secret,
        # which is why the fourth word is named rather than assumed.
        elif (w[:3] == ["ip", "rip", "authentication"]
              and len(w) >= 5 and w[3] == "string"):
            secret = 4
        if secret is not None:
            indent = l[:len(l) - len(l.lstrip())]
            # Everything but the secret is kept, the trailing words included:
            # the key id, the `md5`, and IS-IS's `authenticate snp validate`
            # are what tell an operator WHICH key is set and how it is used.
            out.append(indent + " ".join(
                w[:secret] + ["<set, not shown>"] + w[secret + 1:]))
            continue
        out.append(l)
    return out


def can_anyone_log_in(ssh: dict, users: dict):
    """
    Who could still reach this box over SSH. Returns the list of names.

    An empty list is a configuration that locks everybody out, which is not a
    valid configuration - it is the one mistake an operator makes once, from
    the other side of the country, and cannot undo.

    Console access is deliberately NOT counted. A racked box in a datacentre
    may have a serial console the operator cannot get to today, and "you still
    have the console" is not an answer at 2am.
    """
    root_login = ssh.get("permit-root-login", SSH_DEFAULTS["permit-root-login"])
    passwords_ok = ssh.get("password-authentication",
                           SSH_DEFAULTS["password-authentication"]) == "yes"
    able = []
    for name, u in users.items():
        if name == "root":
            if root_login == "no":
                continue
            if u.keys:
                able.append(name)
            elif u.password and passwords_ok and root_login == "yes":
                able.append(name)
            continue
        if u.keys or (u.password and passwords_ok):
            able.append(name)
    return able


def can_anyone_configure(ssh: dict, users: dict):
    """
    Who could still reach this box over SSH AND change anything. The names.

    The same mistake as locking everybody out, by the other door. Removing the
    last administrator's role leaves a box that answers, forwards and reports,
    and that nobody can configure again through the product: a cgnat operator
    cannot create an account or grant a role, so there is no way back that
    does not involve a root shell somebody may not have.

    root is counted whenever root could log in, because root holds every
    capability by being root.
    """
    return [n for n in can_anyone_log_in(ssh, users)
            if n == "root" or users[n].role == ROLE_ADMIN]


def validate_ssh(settings: dict):
    """Why a setting is refused, or None. Checked before anything is written."""
    for k, v in settings.items():
        if k not in SSH_FIELDS:
            return f"'{k}' is not an ssh setting"
        if k == "port":
            if not is_number(str(v)) or not 1 <= int(v) <= 65535:
                return f"ssh port '{v}': expected 1-65535"
        elif k == "listen-address":
            # NOTHING asked until #413: `ssh listen-address potato` was taken
            # at the prompt, written to the file and handed to sshd, which
            # then refuses its own configuration and does not start - on a box
            # whose other way in is a serial cable.
            why = address_error(f"ssh {k}", str(v))
            if why:
                return why
            # Stored in the spelling sshd -T gives back, so the candidate and
            # the box can agree. See ssh_listen_normal.
            settings[k] = ssh_listen_normal(str(v))
        elif k in _VALID and v not in _VALID[k]:
            return f"ssh {k} '{v}': expected one of " + ", ".join(_VALID[k])
    return None


def dns_error(value):
    """
    None if `value` is a resolver address `dns server` can take, else why not,
    as (verdict, prose). One predicate for both doors (#437).

    An address and not a name: the resolver is what turns names into
    addresses, so it cannot be one. A scoped IPv6 address (`fe80::1%eth0`) is
    refused, because the scope names an interface of THIS box's kernel, which
    the dataplane's interfaces are not.
    """
    if "%" in value:
        return ("dns server: an address without a scope",
                f"'{value}' names an interface of this box's kernel after "
                f"the %, which the dataplane's interfaces are not")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return ("dns server: expected an address",
                f"'{value}' is not an IPv4 or IPv6 address; a resolver is "
                f"what turns names into addresses, so it cannot be one")
    return None


def hostname_error(value):
    """
    None if `value` is a name `hostname` can take, else why not, as
    (verdict, prose). One predicate for the prompt, the file, the door and
    what is read back (#516).

    RFC 1123's labels - letters, digits and hyphens, a hyphen at neither end
    of one, dots between them - which is what the installer accepts, and a
    LETTER FIRST: FRR refuses any other `hostname` ("Please specify string
    starting with alphabet"), and a name half the box took would be two.
    """
    if not value or len(value) > HOSTNAME_MAX:
        return ("hostname: 1 to 64 characters",
                f"'{value}' is {len(value)}; the kernel keeps {HOSTNAME_MAX}")
    if not value[0].isascii() or not value[0].isalpha():
        return ("hostname: starts with a letter",
                f"'{value}' does not, and FRR refuses a name that does not")
    for label in value.split("."):
        if not label or len(label) > 63 or label[0] == "-" \
                or label[-1] == "-" or not all(
                    c.isascii() and (c.isalnum() or c == "-") for c in label):
            return ("hostname: letters, digits and hyphens",
                    f"'{value}' is not: each part between dots is 1 to 63 of "
                    f"them, a hyphen at neither end")
    return None


class LocalFiles:
    """
    Every file the VTY writes on the host, written here (#448, phase B).

    The seam the host's doors replace: doors.DoorFiles has the same methods
    and sends each to the daemon, which accepts only the paths below it. Each
    method raises OSError on failure, as the open() it replaced did, so the
    callers' handling is unchanged. The modes are the ones the callers set
    before the seam existed, and decided here rather than by the caller, so a
    door cannot be asked for another.
    """

    def write(self, path, text, mode=None, atomic=False):
        """`path` made to hold `text`: by rename when `atomic`, else in
        place; its directory made first; `mode` set when given."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        target = path + ".prisma-new" if atomic else path
        with open(target, "w") as f:
            f.write(text)
        if mode is not None:
            os.chmod(target, mode)
        if atomic:
            os.replace(target, path)

    def append(self, path, text, mode=None):
        """`text` added to the end of `path`."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(text)
        if mode is not None:
            os.chmod(path, mode)

    def unlink(self, path):
        """`path` removed."""
        os.unlink(path)

    def copy(self, src, dst):
        """`src` kept as `dst`, times and mode with it."""
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

    def mkdir(self, path):
        """`path` and its parents, if absent."""
        os.makedirs(path, exist_ok=True)

    def write_keys(self, home, text):
        """
        `home`/.ssh/authorized_keys made to hold `text`, 0600 in a 0700 .ssh.

        REFUSED THROUGH A LINK. Written as root into a directory the account
        owns: a `.ssh` or an `authorized_keys` the account had made a symlink
        would have been followed, and the chmod 0700 landed on whatever it
        pointed at. Found reading this for the doors; the old path did it.
        """
        d = os.path.join(home, ".ssh")
        path = os.path.join(d, "authorized_keys")
        for p in (d, path):
            if os.path.islink(p):
                raise OSError(errno.ELOOP, f"{p} is a symbolic link")
        os.makedirs(d, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                     | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(path, 0o600)
        os.chmod(d, 0o700)


class System:
    def __init__(self, runner=None, root="", dropin=SSHD_DROPIN,
                 shell=LOGIN_SHELL, files=None):
        self.root = root
        self.dropin = root + dropin
        self.shell = shell
        self._run = runner or self._subprocess
        self.files = files or LocalFiles()

    @staticmethod
    def _subprocess(argv, stdin=None, timeout=30):
        try:
            p = subprocess.run(argv, capture_output=True, text=True,
                               input=stdin, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except FileNotFoundError:
            return 127, f"{argv[0]}: not found"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]}: timed out"

    # -------------------------------------------------------------- reading

    def read_dns(self) -> list:
        """
        The resolvers `dns server` set, read out of /etc/resolv.conf - or []
        when the file is not the VTY's (#437).

        Read from the file and not remembered, like everything else a running
        configuration reports. And ONLY when its first line says the VTY owns
        it: a box whose resolvers came from DHCP or from the installer has no
        `dns server` statement, and reporting theirs as one would have the
        next `save` pin a lease's answer into the configuration for good.
        """
        try:
            with open(self.root + RESOLV_CONF) as f:
                lines = f.read().splitlines()
        except OSError:
            return []
        if not lines or lines[0] != DNS_OWNED:
            return []
        # What glibc ASKS, and so what survives `save` and the boot's
        # `--load` (test/test_a_readback_survives.py's rule, #357): the first
        # DNS_MAX nameserver lines it can parse. A hand-edited owned file with
        # a fourth server, or a line nobody can resolve through, reported
        # whole would be saved and then refused at boot - the whole file, and
        # a box with nothing on it.
        servers = [w[1] for w in (l.split() for l in lines[1:])
                   if len(w) >= 2 and w[0] == "nameserver"
                   and dns_error(w[1]) is None]
        return servers[:DNS_MAX]

    def write_dns(self, servers):
        """
        Make /etc/resolv.conf say `servers`, or give it back when there are
        none. (ok, message): message is "" when there is nothing to say.

        Taking it over keeps what was there, once, in RESOLV_BEFORE. Giving
        it back restores that and removes the keep, so DHCP or the installer's
        answer is in charge again and the DHCP hook stands down with it. By
        rename, so a resolver lookup never reads half a file. A symlink is
        refused: something else owns the file, and writing through it would be
        a second owner.
        """
        path = self.root + RESOLV_CONF
        before = self.root + RESOLV_BEFORE
        if os.path.islink(path):
            return False, (f"{RESOLV_CONF} is a link to "
                           f"{os.readlink(path)}: something else writes it, "
                           f"so nothing was changed")
        owned = bool(self.read_dns()) or self._owned(path)
        try:
            if servers:
                if not owned and os.path.exists(path) \
                        and not os.path.exists(before):
                    self.files.copy(path, before)
                body = [DNS_OWNED] + [f"nameserver {a}" for a in servers]
                self._replace(path, "\n".join(body) + "\n")
            elif owned:
                if os.path.exists(before):
                    with open(before) as f:
                        self._replace(path, f.read())
                    self.files.unlink(before)
                else:
                    self._replace(path, "")
        except OSError as e:
            return False, f"{RESOLV_CONF}: {e.strerror}"
        return True, ""

    @staticmethod
    def _owned(path) -> bool:
        """Does the file's first line say the VTY owns it?"""
        try:
            with open(path) as f:
                return f.readline().rstrip("\n") == DNS_OWNED
        except OSError:
            return False

    def _replace(self, path, text):
        """Write `path` whole, by rename, mode 0644 - resolv.conf is read by
        every account on the box, and a lookup must never see half of it."""
        self.files.write(path, text, mode=0o644, atomic=True)

    def read_hostname(self) -> str:
        """
        The host's name, asked of the host (#516): the VTY's container has a
        name of its own. "" when it cannot be asked, or when it is one the
        VTY could not write back - reported, it would be saved, and a saved
        statement the file parser refuses is a box that does not load.
        """
        rc, out = self._run(["hostname"])
        name = out.strip() if rc == 0 else ""
        return name if hostname_error(name) is None else ""

    def write_hostname(self, name) -> tuple:
        """
        Rename the host to `name`: (ok, message). The host program checks the
        name again and says what it could not do.
        """
        why = hostname_error(name)
        if why:
            return False, " - ".join(why)
        rc, out = self._run([SET_HOSTNAME, name])
        if rc != 0:
            return False, out.strip() or f"{SET_HOSTNAME} exited {rc}"
        return True, ""

    def read_ssh(self) -> dict:
        s = dict(SSH_DEFAULTS)
        rc, out = self._run(["sshd", "-T"])
        if rc == 0 and out.strip():
            seen = {}
            for line in out.splitlines():
                w = line.split(None, 1)
                if len(w) == 2:
                    seen.setdefault(w[0].strip().lower(), w[1].strip())
            for field, key in _SSHD_T.items():
                if key in seen:
                    v = seen[key]
                    if field == "listen-address":
                        # sshd -T prints "0.0.0.0:22"; the port is its own
                        # setting and repeating it here would put the same
                        # number in two places.
                        v = v.rsplit(":", 1)[0] if ":" in v else v
                    v = _SSHD_T_ALIASES.get(field, {}).get(v, v)
                    s[field] = v
            return s
        # sshd could not be asked - not installed, or refusing its own config.
        # Fall back to the file this VTY owns rather than inventing an answer.
        try:
            with open(self.dropin) as f:
                for line in f:
                    w = line.split(None, 1)
                    if len(w) != 2:
                        continue
                    for field, key in _SSHD_T.items():
                        if w[0].strip().lower() == key:
                            v = w[1].strip()
                            s[field] = _SSHD_T_ALIASES.get(field,
                                                           {}).get(v, v)
        except OSError:
            pass
        return s

    def read_users(self) -> dict:
        """
        The accounts this VTY owns: those whose login shell is the VTY, plus
        root, which is always one of them.

        The shell is the marker rather than a list kept somewhere: a list is a
        second thing to keep in step, and an account that lands in the VTY is
        by definition an account of this appliance.
        """
        users = {}
        members, gid_group = self._read_groups()
        try:
            with open(self.root + "/etc/passwd") as f:
                for line in f:
                    p = line.rstrip("\n").split(":")
                    if len(p) < 7:
                        continue
                    name, gid, home, sh = p[0], p[3], p[5], p[6]
                    if name != "root" and sh != self.shell:
                        continue
                    u = User(name)
                    u.keys = self._read_keys(home)
                    # Read from the box, never remembered. A role kept in the
                    # saved configuration would go on being reported after
                    # somebody removed the membership by hand, and the VTY
                    # would be describing an account it no longer describes
                    # (#161).
                    #
                    # root is skipped rather than examined: its rights do not
                    # come from a group, so a role read off one would be
                    # noise - and this VTY does not manage its memberships.
                    if name != "root":
                        held = members.get(name, set()) \
                            | {gid_group.get(gid, "")}
                        u.role = role_from_groups(held)
                        u.operator = GROUP_OPERATOR in held
                    users[name] = u
        except OSError:
            return users

        shadow = {}
        try:
            with open(self.root + "/etc/shadow") as f:
                for line in f:
                    p = line.split(":")
                    if len(p) > 1:
                        shadow[p[0]] = p[1]
        except OSError:
            pass
        for name, u in users.items():
            h = shadow.get(name, "")
            # "!" and "*" are a locked account, not a password.
            u.password = h if h and not h.startswith(("!", "*")) else ""
        return users

    def _read_groups(self):
        """
        (name -> the groups listing it, gid -> group name), from /etc/group.

        The primary group is returned separately because it is not in any
        member list: an account whose primary group is a role group holds that
        role as far as the system is concerned, so a reader that only looked
        at the member lists would report read-only for an account the box
        lets configure.
        """
        members, gid_group = {}, {}
        try:
            with open(self.root + "/etc/group") as f:
                for line in f:
                    p = line.rstrip("\n").split(":")
                    if len(p) < 4:
                        continue
                    gid_group[p[2]] = p[0]
                    for m in p[3].split(","):
                        if m:
                            members.setdefault(m, set()).add(p[0])
        except OSError:
            pass
        return members, gid_group

    def _read_keys(self, home):
        try:
            with open(f"{self.root}{home}/.ssh/authorized_keys") as f:
                return [l.strip() for l in f
                        if l.strip() and not l.startswith("#")]
        except OSError:
            return []

    # -------------------------------------------------------------- writing

    def write_ssh(self, settings: dict):
        """
        Write the drop-in and VALIDATE IT BEFORE the daemon is asked to use it.

        A syntax error that stops sshd from starting is the same lockout as a
        wrong ListenAddress, by a different route - so the old file is put back
        if `sshd -t` refuses the new one, and nothing is reloaded.
        """
        why = validate_ssh(settings)
        if why:
            return False, why

        body = ["# Generated by prismash. Edit through the VTY, not here.",
                f"Port {settings.get('port', SSH_DEFAULTS['port'])}",
                f"ListenAddress {settings.get('listen-address', SSH_DEFAULTS['listen-address'])}",
                f"PermitRootLogin {settings.get('permit-root-login', SSH_DEFAULTS['permit-root-login'])}",
                f"PasswordAuthentication {settings.get('password-authentication', SSH_DEFAULTS['password-authentication'])}",
                ""]

        previous = None
        try:
            with open(self.dropin) as f:
                previous = f.read()
        except OSError:
            pass

        self.files.write(self.dropin, "\n".join(body))

        # sshd -t refuses to run at all without its privilege separation
        # directory, which systemd normally creates. Without this, a perfectly
        # valid change is reported as "sshd refused the configuration" and
        # rolled back for a reason that has nothing to do with the change.
        try:
            self.files.mkdir(self.root + "/run/sshd")
        except OSError:
            pass

        rc, out = self._run(["sshd", "-t"])
        if rc not in (0, 127):
            if previous is None:
                self.files.unlink(self.dropin)
            else:
                self.files.write(self.dropin, previous)
            # Failing closed on purpose. Refusing a valid change costs an
            # operator a retry; accepting an invalid one costs them the box.
            return False, ("sshd refused the configuration, so nothing was "
                           "applied: " + out.strip()[:200])
        if rc == 127:
            return True, "sshd is not installed: written but not validated"

        rc, out = self._run(["systemctl", "reload", "ssh"])
        if rc not in (0, 127):
            return True, "written and valid, but the daemon did not reload: " \
                         + out.strip()[:120]
        return True, ""

    def apply_users(self, want: dict, have: dict):
        """Create, update and remove accounts. Returns (ok, [messages])."""
        msgs = []
        for name in have:
            if name not in want and name != "root":
                rc, out = self._run(["userdel", "-r", name])
                if rc not in (0, 127):
                    return False, msgs + [f"userdel {name}: {out.strip()[:120]}"]
                msgs.append(f"removed account {name}")

        for name, u in want.items():
            if name not in have and name != "root":
                rc, out = self._run(
                    ["useradd", "-m", "-s", self.shell, name])
                if rc not in (0, 127):
                    return False, msgs + [f"useradd {name}: {out.strip()[:120]}"]
                msgs.append(f"created account {name}")
            elif name != "root":
                # The shell is what marks an account as this appliance's, so
                # it is set every time rather than only at creation: an account
                # whose shell drifted would silently stop being ours.
                self._run(["usermod", "-s", self.shell, name])

            if name != "root":
                # Every time, not only when the role changed: this is what
                # repairs an account whose membership drifted, and it is
                # cheap - the memberships are read from /etc/group and only
                # the difference is written.
                ok, why, gmsgs = self._apply_groups(
                    name, u.role, getattr(u, "operator", False))
                msgs += gmsgs
                if not ok:
                    return False, msgs + [why]

            old = have.get(name)
            if not old or old.keys != u.keys:
                ok, why = self._write_keys(name, u.keys)
                if not ok:
                    return False, msgs + [why]
                msgs.append(f"{name}: {len(u.keys)} authorized key(s)")
            if u.password and (not old or old.password != u.password):
                rc, out = self._run(["chpasswd", "-e"],
                                    stdin=f"{name}:{u.password}\n")
                if rc not in (0, 127):
                    return False, msgs + [f"password for {name}: "
                                          + out.strip()[:120]]
                # Deliberately does not say what it was set to.
                msgs.append(f"{name}: password changed")
        return True, msgs

    def _apply_groups(self, name, role, operator=False):
        """
        Put the account in the groups its role implies, and take it out of the
        ones it no longer has. Returns (ok, why, [messages]).

        The removal is half the point. A demotion that leaves the membership
        behind is not a demotion: the VTY would report read-only and the box
        would go on granting the role, and the account would keep the reach
        the role was the only reason for.
        """
        want = groups_for(role, operator)
        members, gid_group = self._read_groups()
        have = members.get(name, set())
        msgs = []
        if GROUP_OPERATOR in want and GROUP_OPERATOR not in gid_group.values():
            # A group appliance/install.sh does not create yet - that is a
            # medium-only file and #563 shipped as a patch - so the first
            # account granted it creates it, through a door of exactly this
            # shape (doors.FIXED). 0.150.0 moves it to the installer.
            rc, out = self._run(list(OPERATOR_GROUPADD))
            if rc not in (0, 9, 127):          # 9: it already exists
                return False, (f"cannot create group {GROUP_OPERATOR}: "
                               + out.strip()[:120]), msgs
            msgs.append(f"created group {GROUP_OPERATOR}")
        for g in MANAGED_GROUPS:
            if g in want and g not in have:
                rc, out = self._run(["gpasswd", "-a", name, g])
                if rc not in (0, 127):
                    why = f"{name}: cannot join group {g}: {out.strip()[:120]}"
                    # A role group that could not be joined is a role the
                    # operator asked for and did not get, so the commit fails
                    # rather than reporting a rank the box will not honour.
                    # A missing reach group is said out loud and does not stop
                    # the account being created: on a box without FRR there is
                    # no frrvty group to join and no vtysh to run either.
                    if g in (GROUP_ADMIN, GROUP_CGNAT, GROUP_OPERATOR):
                        return False, why, msgs
                    msgs.append(why + " - it cannot use what needs that group")
                    continue
                msgs.append(f"{name}: added to group {g}")
            elif g not in want and g in have:
                rc, out = self._run(["gpasswd", "-d", name, g])
                if rc not in (0, 127):
                    return False, (f"{name}: cannot leave group {g}: "
                                   + out.strip()[:120]), msgs
                msgs.append(f"{name}: removed from group {g}")
        return True, "", msgs

    def _write_keys(self, name, keys):
        home = f"{self.root}/root" if name == "root" else f"{self.root}/home/{name}"
        try:
            self.files.write_keys(home, "".join(k + "\n" for k in keys))
        except OSError as e:
            return False, f"authorized_keys for {name}: {e}"
        return True, ""


# ----------------------------------------------------- listen-address lists

def listen_addresses(value) -> list:
    """
    The addresses a `listen-address` names, in order (#582).

    A LIST, space-separated on the one line the setting has: 10.40.200.166
    (0.151.5) had to answer SNMP and the API on its management interface's
    IPv6 address as well as its IPv4 one, and the setting took a single
    address. One address is a list of one, so every file written before
    reads the same.
    """
    return str(value or "").split()


def listen_error(what: str, value):
    """
    None if `value` is one or more addresses, each named once; otherwise why
    not. Each address is held to `address_error`, the one-address rule.
    """
    addrs = listen_addresses(value)
    if not addrs:
        return f"{what}: expected an address"
    seen = set()
    for a in addrs:
        why = address_error(what, a)
        if why:
            return why
        key = ipaddress.ip_address(unbracket(a))
        if key in seen:
            return f"{what} '{a}': named twice"
        seen.add(key)
    return None


def snmp_agent_address(listen, port) -> str:
    """
    snmpd's agentAddress for `listen` (one or more addresses) on `port`:
    `udp:A:P` for IPv4 and `udp6:[B]:P` for IPv6, comma-separated (#582).
    Written `udp:<v6>:161` before, which snmpd does not read as IPv6.
    """
    return ",".join(
        f"udp6:[{unbracket(a)}]:{port}" if ":" in unbracket(a)
        else f"udp:{a}:{port}"
        for a in listen_addresses(listen))


def parse_agent_address(word: str):
    """
    (addresses as a listen-address value, port) from an agentAddress word, or
    (None, None) when it is not one this VTY writes (#582).
    """
    addrs, port = [], None
    for part in word.split(","):
        if part.startswith("udp6:[") and "]:" in part:
            a, _, p = part[len("udp6:["):].partition("]:")
        elif part.startswith("udp:") and part.count(":") == 2:
            _, a, p = part.split(":")
        else:
            return None, None
        if not is_number(p) or (port is not None and p != port):
            return None, None
        addrs.append(a)
        port = p
    return (" ".join(addrs), port) if addrs else (None, None)


# --------------------------------------------------------------------- SNMP

SNMPD_CONF = "/etc/snmp/snmpd.conf"
SNMP_AGENT = "/usr/lib/prisma-routing/snmp-agent"

SNMP_FIELDS = ("enabled", "listen-address", "port", "enterprise-number")
SNMP_DEFAULTS = {
    "enabled": "no",          # off until an operator asks for it
    "listen-address": "127.0.0.1",
    "port": "161",
    "enterprise-number": "67033",  # PRODUCT_PEN; see below
}
#: The number PRISMA-ROUTING-MIB is served under when the operator names none:
#: the product's own IANA Private Enterprise Number, 67033, assigned on
#: 2026-10-02 (#554). From 0.143.4 (#508) until then it was 32473, IANA's
#: "Example Enterprise Number for Documentation Use" (RFC 5612), because the
#: product had none; the number first proposed, 666, is Oki Electric
#: Industry's. The default is never written into a saved configuration, so
#: every box that did not name its own moved with this one line.
PRODUCT_PEN = SNMP_DEFAULTS["enterprise-number"]


#: The management API's settings (#532), in the order they are written.
API_FIELDS = ("enabled", "listen-address", "port")
API_DEFAULTS = {
    "enabled": "no",            # off until an operator asks for it
    "listen-address": "",       # the management address; no default listener
    "port": "50051",
}
#: Where they live: under the directory the management container shares with
#: the host, read by appliance/api-configure, which writes the listener.
API_ENV = "/etc/prisma-routing/api.env"
#: The door that applies them on the host.
API_CONFIGURE = ("systemctl", "restart", "prisma-routing-api-config.service")


def validate_api(settings: dict):
    """The prompt's and the file's rule for an `api` setting; None when it holds."""
    for k, v in settings.items():
        if k not in API_FIELDS:
            return f"'{k}' is not an api setting"
        if k == "enabled" and v not in ("yes", "no"):
            return f"api enabled '{v}': expected yes or no"
        if k == "port" and (not is_number(str(v)) or not 1 <= int(v) <= 65535):
            return f"api port '{v}': expected 1-65535"
        if k == "listen-address" and v:
            why = listen_error(f"api {k}", str(v))
            if why:
                return why
    return None


def api_enable_error(settings: dict):
    """
    Why these settings cannot be applied as a whole, or None. The API listens
    on the management network only, so enabling it names the address.
    """
    if settings.get("enabled") == "yes" and not settings.get("listen-address"):
        # One row on an 80-column console: the reason - the API listens
        # on the management network only - is the docs' and the help's.
        return "api enabled yes needs 'api listen-address <management address>'"
    return None


def api_env_text(settings: dict) -> str:
    """api.env's text for `settings`."""
    return (f"API_ENABLED={settings.get('enabled', 'no')}\n"
            f"API_LISTEN={settings.get('listen-address', '')}\n"
            f"API_PORT={settings.get('port', API_DEFAULTS['port'])}\n")


class ApiService:
    """
    The management API's settings on the box (#532): read from api.env, and
    applied by writing it and asking the host to apply it.
    """

    def __init__(self, runner=None, path=API_ENV):
        self._run = runner or (lambda argv, stdin=None, timeout=30: (127, ""))
        self.path = path

    def read(self) -> dict:
        """The settings in effect: api.env's, or the defaults without one."""
        out = dict(API_DEFAULTS)
        keys = {"API_ENABLED": "enabled", "API_LISTEN": "listen-address",
                "API_PORT": "port"}
        try:
            with open(self.path) as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if k in keys and validate_api({keys[k]: v}) is None:
                        out[keys[k]] = v
        except OSError:
            pass
        return out

    def apply(self, settings: dict):
        """(ok, message): api.env written whole and the host asked to apply it."""
        why = validate_api(settings) or api_enable_error(settings)
        if why:
            return False, why
        tmp = self.path + ".new"
        try:
            with open(tmp, "w") as f:
                f.write(api_env_text(settings))
            os.replace(tmp, self.path)
        except OSError as e:
            return False, f"{self.path}: {e.strerror}"
        rc, out = self._run(list(API_CONFIGURE))
        # No service manager to ask - no systemctl at all, or one with no
        # systemd behind it, as lab/api-grpc.sh's container answered ("Failed
        # to connect to bus") - is not a refusal: the file is the setting, and
        # prisma-routing-api-config.service applies it at the next boot. Told
        # apart by the answer and not by looking for /run/systemd: from the
        # management container the request goes through the host's door, and
        # the container has no systemd of its own either way.
        if rc == 127 or "Failed to connect to bus" in out \
                or "not been booted with systemd" in out:
            return True, ("written, but the host's service manager is not "
                          "reachable here, so it applies at the next boot")
        if rc != 0:
            return False, (out.strip().splitlines() or ["the host refused"])[-1]
        return True, ""


def validate_snmp(settings: dict):
    for k, v in settings.items():
        if k not in SNMP_FIELDS:
            return f"'{k}' is not an snmp setting"
        if k == "enabled" and v not in ("yes", "no"):
            return f"snmp enabled '{v}': expected yes or no"
        if k == "port" and (not is_number(str(v)) or not 1 <= int(v) <= 65535):
            return f"snmp port '{v}': expected 1-65535"
        if k == "listen-address":
            why = listen_error(f"snmp {k}", str(v))
            if why:
                return why
        if k == "enterprise-number" and v and (
                not is_number(str(v)) or not 1 <= int(v) <= 4294967295):
            # An OID under somebody else's PEN is a lie about who the object
            # belongs to, and it collides the day that organisation uses the
            # same subtree. So there is no default and no guess: either the
            # operator has a number from IANA or the enterprise objects stay
            # dark.
            #
            # The BOUND is new and `?` is the reason for it. A PEN is an OID
            # arc, 1 to 2^32-1, and the VTY was offering this one as
            # `<1-65535>` - which is not the registry's range and never was:
            # IANA has been assigning above 65535 since the 1990s, so an ISP
            # reading that hint saw its own number refused by a screen while
            # the box took it (#351).
            return f"snmp enterprise-number '{v}': expected 1-4294967295"
    return None


#: How a notification destination wants to be told (#507): a trap, sent and
#: forgotten, or an inform, which the receiver acknowledges and the agent
#: resends until it does. A choice per destination, so that nobody had to
#: make it for everybody.
TRAP_KINDS = ("trap", "inform")
#: The VACM names the communities are granted under (#511): v2c only.
SNMP_V2C_SECNAME = "prismaCommunity"
SNMP_V2C_GROUP = "prismaV2c"
#: The user snmpd queries ITSELF as, for the monitors behind the
#: notifications. Not an operator's account: read() leaves it out, or it
#: would come back as one in the running configuration.
SNMP_INTERNAL_USER = "prismaInternal"


def validate_trap_host(address, community, kind="trap"):
    """Why `snmp trap-host` is refused, or None."""
    why = address_error("snmp trap-host", str(address))
    if why:
        return why
    if not community:
        return "snmp trap-host: a community is needed, for the receiver"
    if kind not in TRAP_KINDS:
        return f"snmp trap-host: '{kind}' is not trap or inform"
    return None


class SnmpUser:
    """A v3 user. v2c communities are the other kind and carry no protocols."""

    def __init__(self, name):
        self.name = name
        self.auth = ""        # "sha <passphrase>"
        self.priv = ""        # "aes <passphrase>"

    def lines(self):
        out = [f"snmp-user {self.name}"]
        if self.auth:
            out.append(f" auth {self.auth}")
        if self.priv:
            out.append(f" priv {self.priv}")
        out.append(" exit")
        return out

    def __eq__(self, other):
        return isinstance(other, SnmpUser) and self.lines() == other.lines()


def identity_redact(lines):
    """
    The RADIUS shared secret, blanked for display.

    The same rule as `snmp_redact` below and for the same reason: this secret
    is what tells an accounting packet from anybody's packet, and one on a
    screen shared during an incident has left the building. A display
    transform only - the stored configuration keeps the real value, or a box
    could not reload its own listener (#21).
    """
    # Both spellings: the model's `cgnat identity ...`, and `identity ...`
    # indented under `cgnat`, which is how the configuration is written since
    # #446. Matching only the first would have put the secret back on every
    # `show running-config` the day the file changed shape.
    out = []
    for l in lines:
        w = l.split()
        at_level = w[:1] == ["cgnat"]
        if (w[at_level:at_level + 2] == ["identity", "radius"]
                and "secret" in w):
            at = w.index("secret")
            kept = w[:at + 1] + ["<set, not shown>"] + w[at + 2:]
            out.append(l[:len(l) - len(l.lstrip())] + " ".join(kept))
            continue
        out.append(l)
    return out


def snmp_redact(lines):
    """Community strings and v3 passphrases are passwords on the wire."""
    out = []
    for l in lines:
        s = l.strip()
        if s.startswith("snmp community "):
            out.append(" snmp community <set, not shown>"
                       if l.startswith(" ") else "snmp community <set, not shown>")
        elif s.startswith("snmp trap-host ") and " community " in s:
            # The receiver's community is as much a password as the agent's
            # own (#507): the address stays, the string does not.
            w = s.split()
            at = w.index("community")
            kept = w[:at + 1] + ["<set, not shown>"] + w[at + 2:]
            out.append(l[:len(l) - len(l.lstrip())] + " ".join(kept))
        elif s.startswith(("auth ", "priv ")) and l.startswith(" "):
            out.append(f" {s.split()[0]} {s.split()[1]} <set, not shown>"
                       if len(s.split()) > 2 else l)
        else:
            out.append(l)
    return out


# WHAT AN NMS SEES (#497). The view admitted NAT-MIB's timeouts and the
# operator's subtree and nothing else, so a poller got no sysDescr, no uptime,
# no CPU, memory or disk from a box whose net-snmp serves all of them: the
# system group, HOST-RESOURCES-MIB and UCD-SNMP-MIB are net-snmp's own and
# cost this product nothing to answer. The dataplane's interfaces are not
# here: net-snmp's IF-MIB reads Linux, which does not have them (#496, 1b).
#: IP-MIB's objects the agent serves from the dataplane (#522): ipForwarding,
#: ipAddrTable, ipv6IpForwarding, ipAddressPrefixTable, ipAddressTable.
IP_MIB_SUBTREES = ("1", "20", "25", "32", "34")
#: Where each is registered above net-snmp's module: on the OIDs that module
#: registers, which for ipAddrTable is each of its five columns - one
#: registration on the table lost all but the column net-snmp does not serve,
#: and lab/snmp.sh read Linux's loopback in it.
IP_MIB_REGISTERED = ("1", "20.1.1", "20.1.2", "20.1.3", "20.1.4", "20.1.5",
                     "25", "32", "34")
SNMP_VIEW = [".1.3.6.1.2.1.1",          # SNMPv2-MIB system: sysDescr, uptime
             ".1.3.6.1.2.1.2",          # IF-MIB: ifNumber, ifTable (#498)
             ".1.3.6.1.2.1.31",         # IF-MIB: ifXTable, 64-bit counters
             ".1.3.6.1.2.1.25",         # HOST-RESOURCES-MIB: CPU, storage, processes
             ".1.3.6.1.4.1.2021",       # UCD-SNMP-MIB: load, memory, disks
             ".1.3.6.1.2.1.123",        # NAT-MIB, the timeouts the agent serves
             ".1.3.6.1.2.1.234",        # NATV2-MIB, the pools (#512)
             ".1.3.6.1.2.1.207",        # VRRPV3-MIB, the VRRP routers (#513)
             ".1.2.840.10006.300.43",   # IEEE8023-LAG-MIB, LACP (#515)
             # BFD-STD-MIB (#559): bfdd's sessions, served by the agent from
             # vtysh - FRR 10.7.1 has no bfdd_snmp to serve it over AgentX.
             ".1.3.6.1.2.1.222",
             # IP-MIB (#522): the forwarding scalars and the address tables,
             # the agent's; the rest of IP-MIB is Linux's and stays out.
             *(f".1.3.6.1.2.1.4.{sub}" for sub in IP_MIB_SUBTREES),
             # The routing protocols, which FRR's daemons serve themselves
             # over AgentX (#502).
             ".1.3.6.1.2.1.15",         # BGP4-MIB: bgpd's peers and their state
             ".1.3.6.1.2.1.14",         # OSPF-MIB: ospfd
             ".1.3.6.1.2.1.191",        # OSPFV3-MIB: ospf6d
             ".1.3.6.1.2.1.138",        # ISIS-MIB: isisd
             ".1.3.6.1.3.5.1",          # BGP4V2-MIB: bgpd, IPv6 peers too (#521)
             # The FIB's SIZE and not the FIB (#503): a full table is about a
             # million routes, and a walk of it is never the default view
             # (docs/snmp-plan.md). Measured in lab/igp.sh: .24.3 counts IPv4
             # (6 there) and .24.6 IPv4 and IPv6 (14), read by net-snmp off
             # the kernel, where FRR installs what linux_nl copies to VPP.
             # ipCidrRouteNumber: in the view, and Debian's net-snmp answers
             # it noSuchObject (#514's Zabbix run); inetCidrRouteNumber below
             # is the one that counts.
             ".1.3.6.1.2.1.4.24.3",
             ".1.3.6.1.2.1.4.24.6"]     # IP-FORWARD-MIB inetCidrRouteNumber
#: Where snmpd listens for FRR's daemons (#502). A unix socket and not TCP 705,
#: which any local account could register a subtree on; the directory is the
#: box's frr group's, and FRR's container is given it (appliance/compose.yaml).
AGENTX_SOCKET = "/var/agentx/master"
# The filesystems whose filling an NMS alarms on (UCD's dskTable), with the
# free space below which dskErrorFlag is raised. The retention log's is the
# one that matters: a full disk there is the compliance log stopping, a year
# of connection records under a court's obligation (docs/logging.md).
SNMP_DISKS = [("/", "10%"), ("/var/log/prisma-routing", "15%")]
#: The account Debian's snmpd runs as, and so the one that has to reach the
#: dataplane's sockets (#499).
SNMPD_USER = "Debian-snmp"
SNMP_VERSION_FILE = os.environ.get("PRISMA_VERSION_FILE",
                                   "/usr/share/prisma-routing/VERSION")


def _release():
    """The release this box runs, for sysDescr, or '' when it cannot say."""
    try:
        with open(SNMP_VERSION_FILE) as f:
            return f.read().split()[0]
    except (OSError, IndexError):
        return ""


def _sink(address):
    """snmpd's transport for a notification destination: udp6 in brackets."""
    return f"udp6:[{address}]:162" if ":" in address else address


def _unsink(word):
    """The address a trap2sink/informsink transport names (see _sink)."""
    if word.startswith("udp6:[") and "]" in word:
        return word[len("udp6:["):word.index("]")]
    return word


def snmp_monitors(pen):
    """
    DISMAN-EVENT-MIB `monitor` lines on PRISMA-ROUTING-MIB (#507): snmpd
    polls its own objects and sends mteTriggerFired, named, when a test
    holds. `-D` compares the change since the last poll, which is what makes
    "a VRRP router changed state" a test at all; `-o` adds the row's name to
    the notification, so the NMS is told WHICH router, pool or stream.
    `-I` on the two scalars: without it snmpd takes the OID as a subtree to
    walk, finds nothing under `.5.0`, and the monitor never fires - measured,
    eight refusals counted and no notification, where every table column's
    monitor fired.
    """
    b = f".1.3.6.1.4.1.{pen}.1"
    return [
        f"monitor -r 30 -D -o {b}.13.1.1.2 -o {b}.13.1.1.3 -o {b}.13.1.1.5 "
        f"vrrpStateChange {b}.13.1.1.5 != 0",
        f"monitor -r 60 -o {b}.10.1.1.2 -o {b}.10.1.1.12 "
        f"poolNearlyFull {b}.10.1.1.12 >= 90",
        f"monitor -r 60 -I -D portsRefused {b}.5.0 > 0",
        f"monitor -r 30 -D -o {b}.11.10.1.2 -o {b}.11.10.1.4 "
        f"recordsTransportChange {b}.11.10.1.4 != 0",
        f"monitor -r 60 -D -o {b}.11.10.1.2 recordsLost {b}.11.10.1.8 > 0",
        f"monitor -r 30 -D -o {b}.14.1.1.2 -o {b}.14.1.1.5 "
        f"bundleMembersChange {b}.14.1.1.5 != 0",
        f"monitor -r 60 -I dataplaneNotCurrent {b}.12.3.0 == 2",
        # The box's own state (#524): a self-test that failed, a commit
        # confirmed nobody confirmed and the box reverted, a tuning that
        # waits for a reboot, and a configuration trail that no longer
        # verifies (broken, truncated or incomplete are 3 and up).
        f"monitor -r 60 -I selfTestFailed {b}.17.2.0 > 0",
        f"monitor -r 60 -I -D configReverted {b}.18.6.0 > 0",
        f"monitor -r 300 -I tuningBetweenPlans {b}.16.1.0 == 2",
        f"monitor -r 300 -I auditTrailDamaged {b}.19.1.0 >= 3",
    ]


def _snmpd_body(settings, communities, users, agent=SNMP_AGENT, release=None,
                trap_hosts=(), hostname=""):
    """The snmpd.conf this VTY owns. Every line is generated; none is copied."""
    # No number means the product's (#508). A running configuration read off a
    # file with no enterprise line says "" - measured, the file Debian ships
    # and every one written before #508 - and a candidate copied from it
    # carried the "" into the file the commit wrote, so the MIB was served by
    # nobody on exactly the boxes the default was for.
    pen = settings.get("enterprise-number", "") or PRODUCT_PEN
    rel = _release() if release is None else release
    out = ["# Generated by prismash. Edit through the VTY, not here.",
           "agentAddress " + snmp_agent_address(
               settings.get("listen-address", SNMP_DEFAULTS["listen-address"]),
               settings.get("port", SNMP_DEFAULTS["port"]))]
    # THE AGENTX MASTER, for FRR's daemons (#502). Measured on the dev image
    # with snmpd running as Debian-snmp: the socket is made before the daemon
    # drops to that user, owned as agentXPerms says, and bgpd and ospfd
    # registered on it and answered BGP4-MIB and OSPF-MIB through it.
    out += ["master agentx", f"agentXSocket {AGENTX_SOCKET}",
            "agentXPerms 0660 0750 frr frr"]
    out += [f"view hexa included {oid}" for oid in SNMP_VIEW]
    if pen:
        out.append(f"view hexa included .1.3.6.1.4.1.{pen}")
    out.append("sysdescr Prisma Routing" + (f" {rel}" if rel else ""))
    # The box's name as sysName (#516), written rather than left to net-snmp,
    # which reads the kernel's once, when it starts: a rename would answer
    # the old name until something restarted it.
    if hostname:
        out.append(f"sysname {hostname}")
    out += [f"disk {path} {free}" for path, free in SNMP_DISKS]
    # READ-ONLY, at the daemon as well as in the agent: every `access` line
    # has no write view ("none" in the write column) and every user is a
    # `rouser`; there is no rwcommunity line anywhere and there must never be
    # one.
    # An SNMP SET that reconfigures a CGNAT is an outage one typo away, and
    # configuration belongs to the VTY where it is validated and rollback-able.
    # v2c AND NOT v1 (#511), by the maintainer's decision. `rocommunity`
    # grants v1 and v2c alike, and v1 cannot carry a Counter64 - IF-MIB's HC
    # columns and the product MIB's counters are invisible to it - while its
    # community travels exactly as v2c's does. So each community goes through
    # VACM: a security name, a group bound to the v2c model only, and a
    # read-only access on the view. A v1 request with the right community is
    # answered as an unknown one.
    for c in communities:
        out.append(f"com2sec {SNMP_V2C_SECNAME} default {c}")
        # And its IPv6 twin (#584): net-snmp's com2sec maps IPv4 sources only,
        # so with an IPv6 listen-address (#582) a v6 query timed out -
        # measured on 10.40.200.166 - until com2sec6 was added beside it.
        out.append(f"com2sec6 {SNMP_V2C_SECNAME} default {c}")
    if communities:
        out.append(f"group {SNMP_V2C_GROUP} v2c {SNMP_V2C_SECNAME}")
        out.append(f'access {SNMP_V2C_GROUP} "" v2c noauth exact hexa none none')
    for name in sorted(users):
        out.append(f"rouser {name} priv -V hexa")
    # THE INTERFACE TABLE IS THE AGENT'S, not net-snmp's (#498): net-snmp's
    # IF-MIB reads Linux, which does not have the dataplane's cards. -p 10 puts
    # these registrations above its module (priority 127) - but only on the
    # very OIDs it registers: one pass_persist on .1.3.6.1.2.1.2 lost to its
    # more specific ifTable, and lab/snmp.sh walked lo and eth0.
    out.append(f"pass_persist -p 10 .1.3.6.1.2.1.2.1 {agent} --interfaces")
    out.append(f"pass_persist -p 10 .1.3.6.1.2.1.2.2 {agent} --interfaces")
    out.append(f"pass_persist -p 10 .1.3.6.1.2.1.31.1.1 {agent} --interfaces")
    # IP-MIB's address tables, from the dataplane under the same ifIndex
    # (#522): net-snmp's list the linux_cp taps under their Linux ifIndex,
    # which no row above has. Above its module the same way, on the OIDs it
    # registers.
    for sub in IP_MIB_REGISTERED:
        out.append(f"pass_persist -p 10 .1.3.6.1.2.1.4.{sub} {agent}")
    out.append(f"pass_persist .1.3.6.1.2.1.123 {agent}")
    out.append(f"pass_persist .1.3.6.1.2.1.234 {agent}")
    out.append(f"pass_persist .1.3.6.1.2.1.207 {agent}")
    out.append(f"pass_persist .1.2.840.10006.300.43 {agent}")
    # BFD-STD-MIB (#559), its own process: it reads bfdd, not the dataplane,
    # and answers while the dataplane does not.
    out.append(f"pass_persist .1.3.6.1.2.1.222 {agent} --bfd")
    if pen:
        # The number on the agent's own command line (#499): it runs as
        # Debian-snmp and cannot read this 0600 file to find it.
        out.append(f"pass_persist .1.3.6.1.4.1.{pen} {agent} --enterprise {pen}")
    if trap_hosts:
        # NOTIFICATIONS (#507). snmpd polls its own objects to decide what to
        # send - as the user named below, read-only on the same view - and
        # sends to each destination the way it asked: a trap, or an inform
        # that is resent until acknowledged.
        out.append(f"iquerySecName {SNMP_INTERNAL_USER}")
        out.append(f"rouser {SNMP_INTERNAL_USER} noauth -V hexa")
        for a, c, kind in trap_hosts:
            out.append(f"{'informsink' if kind == 'inform' else 'trap2sink'} "
                       f"{_sink(a)} {c}")
        # linkUp/linkDown from ifOperStatus, which is the agent's IF-MIB: the
        # dataplane's cards (measured, a card taken down arrived as linkDown).
        out.append("linkUpDownNotifications yes")
        # net-snmp's own: the disks (the retention log's among them), memory,
        # load.
        out.append("defaultMonitors yes")
        if pen:
            out += snmp_monitors(pen)
    out.append("")
    return "\n".join(out)


class SnmpDaemon:
    """
    snmpd, configured from the VTY.

    Validated by EFFECT rather than by a syntax check: net-snmp has no
    equivalent of "sshd -t", so the file is written, the daemon is restarted,
    and the daemon is asked whether it came up. A configuration that parses and
    leaves no agent listening is the failure that matters, and only the effect
    shows it.
    """

    def __init__(self, runner=None, root="", conf=SNMPD_CONF,
                 agent=SNMP_AGENT, files=None):
        self.root = root
        self.conf = root + conf
        # The path the host's door is asked for: the box's own, whatever
        # root this process reads under (#525).
        self.conf_on_host = conf
        self.unread = False
        self.agent = agent
        self._run = runner or System._subprocess
        self.files = files or LocalFiles()

    def _conf(self):
        """
        ("text", the file) | ("absent", None) | ("unread", None): snmpd.conf
        as this process can see it (#525).

        Opened directly where it can be, and asked of the host's door where it
        cannot: the management container does not mount /etc/snmp, and the
        file is 0600 root on the host too. Only an answer is believed. A file
        the door says is not there is SNMP off; a refusal, or no door, is
        UNREAD - which is not the same thing, and was read as "off" until
        #525: on the X520 bench a `save` from the container wrote
        `snmp enabled no` into the file and the next boot deleted snmpd.conf
        and its community.
        """
        try:
            with open(self.conf) as f:
                return "text", f.read()
        except FileNotFoundError:
            # On the host a missing file is an answer: SNMP is off. In the
            # management container it is not - compose.yaml sets
            # PRISMA_IN_MGMT there and mounts no /etc/snmp - so only the host
            # can say it.
            if not os.environ.get("PRISMA_IN_MGMT"):
                return "absent", None
        except OSError:
            pass
        rc, out = self._run(["cat", self.conf_on_host])
        if rc == 0:
            return "text", out
        # "No such file" is believed only from the host: with no door
        # listening the runner runs `cat` HERE, and in the container, which
        # has no /etc/snmp at all, every file is missing.
        if "No such file" in (out or "") and self._host_answers():
            return "absent", None
        return "unread", None

    def _host_answers(self):
        """Does this process's runner reach the host's door right now?"""
        door = getattr(self._run, "door", None)
        return door is not None and os.path.exists(door)

    def read(self):
        """
        (settings, communities, users, trap_hosts) as the file on disk
        describes them. `self.unread` says whether it could be read at all:
        when it could not, what comes back is the defaults and MUST NOT be
        taken for the box's configuration (#525).
        """
        settings = dict(SNMP_DEFAULTS)
        communities, users, traps = [], {}, []
        state, text = self._conf()
        self.unread = state == "unread"
        if state != "text":
            return settings, communities, users, traps
        settings["enabled"] = "yes"
        # What the FILE serves, not the default (#508): a box configured before
        # the product had a number serves no enterprise subtree, and reading it
        # as the default would make the next load see no change to apply.
        settings["enterprise-number"] = ""
        for line in text.splitlines():
            w = line.split()
            if not w:
                continue
            if w[0] == "agentAddress" and len(w) > 1:
                # "udp:0.0.0.0:161", or a list with udp6:[...] (#582)
                listen, port = parse_agent_address(w[1])
                if listen:
                    settings["listen-address"] = listen
                    settings["port"] = port
            elif w[0] == "rocommunity" and len(w) > 1:
                # A file written before #511.
                communities.append(w[1])
            elif (w[0] == "com2sec" and len(w) > 3
                  and w[1] == SNMP_V2C_SECNAME):
                communities.append(w[3])
            elif w[0] == "rouser" and len(w) > 1:
                if w[1] != SNMP_INTERNAL_USER:
                    users.setdefault(w[1], SnmpUser(w[1]))
            elif w[0] in ("trap2sink", "informsink") and len(w) > 2:
                traps.append((_unsink(w[1]), w[2],
                              "inform" if w[0] == "informsink" else "trap"))
            elif w[0] == "pass_persist" and len(w) > 1:
                # From the agent's own registration, not from a view line:
                # the view carries UCD-SNMP-MIB's .1.3.6.1.4.1.2021 too, and
                # reading the first enterprise OID there would take net-snmp's
                # number for the operator's (#497).
                if w[1].startswith(".1.3.6.1.4.1."):
                    settings["enterprise-number"] = w[1].rsplit(".", 1)[-1]
        # The service state answers "is it running", which is not the same
        # question as "is it configured", and on a system systemd is not
        # managing it cannot answer either. Consulted only where it means
        # something; otherwise the file this VTY wrote is the answer.
        #
        # Getting this wrong was not cosmetic: the running configuration came
        # back with enabled=no, the next commit inherited it, and applying that
        # candidate deleted the configuration the previous commit had just
        # written.
        if os.path.isdir(self.root + "/run/systemd/system"):
            rc, _ = self._run(["systemctl", "is-active", "--quiet", "snmpd"])
            if rc not in (0, 127):
                settings["enabled"] = "no"
        return settings, communities, users, traps

    def stale(self, settings, communities, users, trap_hosts=(), hostname=""):
        """
        Does the snmpd.conf on disk differ from the one these settings would
        write? False when SNMP is off, and when the file cannot be read -
        from the management container it cannot, and a question that cannot
        be answered is not a reason to restart the daemon.

        WHY IT IS ASKED AT ALL: the file is written only when an SNMP setting
        changes, and what a release puts in it changes with the release - the
        view, the agent's registrations, sysDescr's release. An upgraded box
        kept the file the release before wrote: measured on the X520 bench
        after 0.144.2 -> 0.145.1, it served none of VRRPV3-MIB, the LAG and
        BGP4V2 MIBs or IP-MIB, and said sysDescr 0.144.2, until somebody
        changed a community.
        """
        if settings.get("enabled", "no") != "yes" or (not communities
                                                      and not users):
            return False
        state, on_disk = self._conf()
        if state == "unread":
            return False
        return on_disk != _snmpd_body(settings, communities, users, self.agent,
                                      trap_hosts=trap_hosts, hostname=hostname)

    def apply(self, settings, communities, users, trap_hosts=(), hostname=""):
        why = validate_snmp(settings)
        if why:
            return False, why
        if settings.get("enabled", "no") != "yes":
            self._run(["systemctl", "disable", "--now", "snmpd"])
            try:
                self.files.unlink(self.conf)
            except OSError:
                pass
            return True, ""

        if not communities and not users:
            return False, ("snmp is enabled with no community and no v3 user: "
                           "nothing could poll it, so this is a listener "
                           "nobody can use")

        previous = None
        try:
            with open(self.conf) as f:
                previous = f.read()
        except OSError:
            pass

        self.files.write(self.conf,
                         _snmpd_body(settings, communities, users, self.agent,
                                     trap_hosts=trap_hosts, hostname=hostname),
                         mode=0o600)

        # THE AGENT HAS TO REACH THE DATAPLANE (#499). snmpd runs as
        # Debian-snmp, in no group but its own, and VPP's sockets are
        # srwxrwxr-x root:vpp: measured as that user, vppctl said "connect:
        # Permission denied" and vpp_get_stats could not connect, so every
        # CGNAT object answered as absent - which is also how a stopped
        # dataplane answers. Before the restart, because a group is read
        # when the process starts; gpasswd -a is a no-op on a member.
        self._run(["gpasswd", "-a", SNMPD_USER, "vpp"])
        # NOT bfdd's group (#571). 0.150.10 put Debian-snmp in frrvty for
        # BFD-STD-MIB, and it reached nothing: on a box running the
        # containers the .vty sockets are frr:frr 0770 (10.40.200.166). And
        # where they ARE frrvty's, that membership is vtysh's whole CLI,
        # configuration included. The agent reads bfdd through the host's
        # door instead (doors.BFD_PEERS_ARGV), so the membership 0.150.10
        # granted is taken back here; gpasswd -d on a non-member is an error
        # nobody needs to read.
        self._run(["gpasswd", "-d", SNMPD_USER, "frrvty"])

        # v3 users are created in net-snmp's own persistent store, which it
        # rewrites on first use. Written only when absent, or every restart
        # would re-create a user that already exists and the daemon would
        # refuse the duplicate.
        self._create_v3_users(users)

        # Whether systemd is managing this box at all, tested structurally
        # rather than by reading systemctl's complaint. Without this, a
        # perfectly good configuration is rolled back on any system where
        # systemctl exists but has no init to talk to - the daemon "did not
        # come up" for a reason that has nothing to do with the file.
        if not os.path.isdir(self.root + "/run/systemd/system"):
            return True, ("written, but systemd is not managing this system, "
                          "so snmpd was not restarted")

        self._run(["systemctl", "enable", "snmpd"])
        rc, out = self._run(["systemctl", "restart", "snmpd"])
        if rc == 127:
            return True, "systemctl not found: written but not started"
        rc, _ = self._run(["systemctl", "is-active", "--quiet", "snmpd"])
        if rc != 0:
            if previous is None:
                try:
                    self.files.unlink(self.conf)
                except OSError:
                    pass
            else:
                self.files.write(self.conf, previous, mode=0o600)
            self._run(["systemctl", "restart", "snmpd"])
            return False, ("snmpd did not come up with that configuration, so "
                           "the previous one was put back")
        return True, ""

    def _create_v3_users(self, users):
        path = self.root + "/var/lib/snmp/snmpd.conf"
        try:
            existing = ""
            if os.path.exists(path):
                with open(path) as f:
                    existing = f.read()
            add = []
            for name, u in sorted(users.items()):
                if f"usmUser" in existing and f'"{name}"' in existing:
                    continue
                if f"createUser {name} " in existing:
                    continue
                a = u.auth.split()
                p = u.priv.split()
                if len(a) < 2:
                    continue
                line = f"createUser {name} {a[0].upper()} \"{a[1]}\""
                if len(p) >= 2:
                    line += f" {p[0].upper()} \"{p[1]}\""
                add.append(line)
            if add:
                self.files.append(path, "\n".join(add) + "\n", mode=0o600)
        except OSError:
            pass
