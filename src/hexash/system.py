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


class User:
    def __init__(self, name):
        self.name = name
        self.keys: list[str] = []
        self.password = ""      # crypt(3) hash, or "" for none

    def lines(self) -> list[str]:
        out = [f"user {self.name}"]
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
                            s[field] = w[1].strip()
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
        try:
            with open(self.root + "/etc/passwd") as f:
                for line in f:
                    p = line.rstrip("\n").split(":")
                    if len(p) < 7:
                        continue
                    name, home, sh = p[0], p[5], p[6]
                    if name != "root" and sh != self.shell:
                        continue
                    u = User(name)
                    u.keys = self._read_keys(home)
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
        if k == "enterprise-number" and v and not str(v).isdigit():
            # An OID under somebody else's PEN is a lie about who the object
            # belongs to, and it collides the day that organisation uses the
            # same subtree. So there is no default and no guess: either the
            # operator has a number from IANA or the enterprise objects stay
            # dark.
            return (f"snmp enterprise-number '{v}': expected the IANA Private "
                    "Enterprise Number of your organisation, a plain integer")
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
