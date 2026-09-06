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
        if l.strip().startswith("password "):
            out.append(" password <set, not shown>")
        else:
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
