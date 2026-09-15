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

import os
import subprocess

SSHD_DROPIN = "/etc/ssh/sshd_config.d/hexarouting.conf"
LOGIN_SHELL = "/usr/bin/hexash-login"

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

GROUP_ADMIN = "hexa-admin"
GROUP_CGNAT = "hexa-cgnat"

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

# hexa-admin is ADDITIVE on top of hexa-cgnat rather than a superset by name:
# /etc/hexarouting and the rollback store under it are group hexa-cgnat mode
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
MANAGED_GROUPS = (GROUP_ADMIN, GROUP_CGNAT) + REACH_GROUPS


def groups_for(role) -> set:
    """The memberships an account with this role must have."""
    return set(REACH_GROUPS) | set(ROLE_GROUPS.get(role, ()))


def role_from_groups(names) -> str:
    """
    The role a set of group names amounts to, or "" for read-only.

    Deliberately the same two questions, in the same order, that
    hexash.capabilities() asks of the running process - a running
    configuration that named a role the VTY would not grant would be a report
    of something that is not true.
    """
    if GROUP_ADMIN in names:
        return ROLE_ADMIN
    if GROUP_CGNAT in names:
        return ROLE_CGNAT
    return ""


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


class User:
    def __init__(self, name):
        self.name = name
        self.keys: list[str] = []
        self.password = ""      # crypt(3) hash, or "" for none
        self.role = ""          # "" is read-only: no role group at all

    def lines(self) -> list[str]:
        out = [f"user {self.name}"]
        if self.role:
            out.append(f" role {self.role}")
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
            if not str(v).isdigit() or not 1 <= int(v) <= 65535:
                return f"ssh port '{v}': expected 1-65535"
        elif k in _VALID and v not in _VALID[k]:
            return f"ssh {k} '{v}': expected one of " + ", ".join(_VALID[k])
    return None


class System:
    def __init__(self, runner=None, root="", dropin=SSHD_DROPIN,
                 shell=LOGIN_SHELL):
        self.root = root
        self.dropin = root + dropin
        self.shell = shell
        self._run = runner or self._subprocess

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
                        u.role = role_from_groups(
                            members.get(name, set())
                            | {gid_group.get(gid, "")})
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

        body = ["# Generated by hexash. Edit through the VTY, not here.",
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

        os.makedirs(os.path.dirname(self.dropin), exist_ok=True)
        with open(self.dropin, "w") as f:
            f.write("\n".join(body))

        # sshd -t refuses to run at all without its privilege separation
        # directory, which systemd normally creates. Without this, a perfectly
        # valid change is reported as "sshd refused the configuration" and
        # rolled back for a reason that has nothing to do with the change.
        try:
            os.makedirs(self.root + "/run/sshd", exist_ok=True)
        except OSError:
            pass

        rc, out = self._run(["sshd", "-t"])
        if rc not in (0, 127):
            if previous is None:
                os.unlink(self.dropin)
            else:
                with open(self.dropin, "w") as f:
                    f.write(previous)
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
                ok, why, gmsgs = self._apply_groups(name, u.role)
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

    def _apply_groups(self, name, role):
        """
        Put the account in the groups its role implies, and take it out of the
        ones it no longer has. Returns (ok, why, [messages]).

        The removal is half the point. A demotion that leaves the membership
        behind is not a demotion: the VTY would report read-only and the box
        would go on granting the role, and the account would keep the reach
        the role was the only reason for.
        """
        want = groups_for(role)
        members, _ = self._read_groups()
        have = members.get(name, set())
        msgs = []
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
                    if g in (GROUP_ADMIN, GROUP_CGNAT):
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
        d = os.path.join(home, ".ssh")
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "authorized_keys")
            with open(path, "w") as f:
                f.write("".join(k + "\n" for k in keys))
            os.chmod(path, 0o600)
            os.chmod(d, 0o700)
        except OSError as e:
            return False, f"authorized_keys for {name}: {e}"
        return True, ""


# --------------------------------------------------------------------- SNMP

SNMPD_CONF = "/etc/snmp/snmpd.conf"
SNMP_AGENT = "/usr/lib/hexarouting/snmp-agent"

SNMP_FIELDS = ("enabled", "listen-address", "port", "enterprise-number")
SNMP_DEFAULTS = {
    "enabled": "no",          # off until an operator asks for it
    "listen-address": "127.0.0.1",
    "port": "161",
    "enterprise-number": "",  # no default is possible; see below
}


def validate_snmp(settings: dict):
    for k, v in settings.items():
        if k not in SNMP_FIELDS:
            return f"'{k}' is not an snmp setting"
        if k == "enabled" and v not in ("yes", "no"):
            return f"snmp enabled '{v}': expected yes or no"
        if k == "port" and (not str(v).isdigit() or not 1 <= int(v) <= 65535):
            return f"snmp port '{v}': expected 1-65535"
        if k == "enterprise-number" and v and (
                not str(v).isdigit() or not 1 <= int(v) <= 4294967295):
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


def snmp_redact(lines):
    """Community strings and v3 passphrases are passwords on the wire."""
    out = []
    for l in lines:
        s = l.strip()
        if s.startswith("snmp community "):
            out.append(" snmp community <set, not shown>"
                       if l.startswith(" ") else "snmp community <set, not shown>")
        elif s.startswith(("auth ", "priv ")) and l.startswith(" "):
            out.append(f" {s.split()[0]} {s.split()[1]} <set, not shown>"
                       if len(s.split()) > 2 else l)
        else:
            out.append(l)
    return out


def _snmpd_body(settings, communities, users, agent=SNMP_AGENT):
    """The snmpd.conf this VTY owns. Every line is generated; none is copied."""
    pen = settings.get("enterprise-number", "")
    out = ["# Generated by hexash. Edit through the VTY, not here.",
           "agentAddress udp:%s:%s" % (
               settings.get("listen-address", SNMP_DEFAULTS["listen-address"]),
               settings.get("port", SNMP_DEFAULTS["port"])),
           "view hexa included .1.3.6.1.2.1.123"]
    if pen:
        out.append(f"view hexa included .1.3.6.1.4.1.{pen}")
    # READ-ONLY, at the daemon as well as in the agent. rocommunity and rouser
    # only: there is no rwcommunity line anywhere and there must never be one.
    # An SNMP SET that reconfigures a CGNAT is an outage one typo away, and
    # configuration belongs to the VTY where it is validated and rollback-able.
    for c in communities:
        out.append(f"rocommunity {c} default -V hexa")
    for name in sorted(users):
        out.append(f"rouser {name} priv -V hexa")
    out.append(f"pass_persist .1.3.6.1.2.1.123 {agent}")
    if pen:
        out.append(f"pass_persist .1.3.6.1.4.1.{pen} {agent}")
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
                 agent=SNMP_AGENT):
        self.root = root
        self.conf = root + conf
        self.agent = agent
        self._run = runner or System._subprocess

    def read(self):
        """(settings, communities, users) as the file on disk describes them."""
        settings = dict(SNMP_DEFAULTS)
        communities, users = [], {}
        try:
            with open(self.conf) as f:
                text = f.read()
        except OSError:
            return settings, communities, users
        settings["enabled"] = "yes"
        for line in text.splitlines():
            w = line.split()
            if not w:
                continue
            if w[0] == "agentAddress" and len(w) > 1:
                # "udp:0.0.0.0:161"
                parts = w[1].split(":")
                if len(parts) == 3:
                    settings["listen-address"] = parts[1]
                    settings["port"] = parts[2]
            elif w[0] == "rocommunity" and len(w) > 1:
                communities.append(w[1])
            elif w[0] == "rouser" and len(w) > 1:
                users.setdefault(w[1], SnmpUser(w[1]))
            elif w[0] == "view":
                for field in w[1:]:
                    if field.startswith(".1.3.6.1.4.1."):
                        settings["enterprise-number"] = field.rsplit(".", 1)[-1]
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
        return settings, communities, users

    def apply(self, settings, communities, users):
        why = validate_snmp(settings)
        if why:
            return False, why
        if settings.get("enabled", "no") != "yes":
            self._run(["systemctl", "disable", "--now", "snmpd"])
            try:
                os.unlink(self.conf)
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

        os.makedirs(os.path.dirname(self.conf), exist_ok=True)
        with open(self.conf, "w") as f:
            f.write(_snmpd_body(settings, communities, users, self.agent))
        os.chmod(self.conf, 0o600)

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
                    os.unlink(self.conf)
                except OSError:
                    pass
            else:
                with open(self.conf, "w") as f:
                    f.write(previous)
            self._run(["systemctl", "restart", "snmpd"])
            return False, ("snmpd did not come up with that configuration, so "
                           "the previous one was put back")
        return True, ""

    def _create_v3_users(self, users):
        path = self.root + "/var/lib/snmp/snmpd.conf"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
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
                with open(path, "a") as f:
                    f.write("\n".join(add) + "\n")
                os.chmod(path, 0o600)
        except OSError:
            pass
