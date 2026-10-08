#!/bin/sh
# The SSH daemon and the accounts that may log in, from the VTY.
#
# This is the one configuration that can lock the operator out of the box they
# are configuring, from the other side of the country. The assertion that
# matters is therefore not that a user can be created - it is that a change
# which strands everybody is REFUSED, and that is verified by producing the
# lockout rather than by reasoning about it.
#
#   sh tools/lab.sh vty-ssh        the lab
#   sh tools/lab.sh vty-ssh off    section 6 through the writer of before
#                                  #593 (lab/vty_ssh_hook.py's LISTEN=off):
#                                  one ListenAddress, 0.0.0.0 for none
#
# Section 6 (#593) is a real sshd on the drop-in the VTY writes, reloaded as
# systemd reloads it (SIGHUP), asked over IPv4 and IPv6: a two-address list,
# one address of each family, no setting (`no ssh listen-address`, and a
# saved file without the line), a file saved before #593, and an address
# the box does not have.
#
# OFF-RED-AT-LEAST: 12
#
# A FLOOR, measured on 2026-10-08 (#593): the off run red on 12 checks, all
# in section 6 - the list's IPv6 half missing from the drop-in, sshd -T and
# show running-config and unanswered (4); `no ssh listen-address` and the
# file without the line writing `ListenAddress 0.0.0.0`, IPv6 unanswered
# (6); the file saved before #593 taking IPv4 away (1); and the refused
# commit leaving that 0.0.0.0 in the drop-in (1). Green there and rightly:
# one address of either family, and the refusal, which is the commit
# check's and not the writer's.
set -u
MODE=${1:-on}
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /etc/ssh/sshd_config.d
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
if [ "$MODE" = off ]; then
  echo "  MODE=off: the sshd writer of before #593. Section 6 MUST go red."
  HX="python3 /src/lab/vty_ssh_hook.py"
  export LISTEN=off
fi
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
# The VTY marks its accounts by their login shell, so it has to exist.
install -D -m 0755 /src/appliance/prismash-login /usr/bin/prismash-login
ssh-keygen -A >/dev/null 2>&1
# sshd -t and sshd -T refuse to run without their privilege separation
# directory, which systemd creates on a box. Nothing creates it in this
# container, and the openssh-server package lab.sh installs on every run does
# not either: `sshd -t` answered "Missing privilege separation directory:
# /run/sshd", and three checks below were red over the fixture (#434).
mkdir -p /run/sshd

vpp -c "$(startup_conf test)" > /tmp/vpp.log 2>&1 &
wait_vpp

# A password for root, so the box starts in a state where somebody CAN log in.
# Without that the lockout check has nothing to protect and the test would be
# asserting against a box that was already stranded.
HASH=$(openssl passwd -6 lab-root-secret)
$HX -c "configure" -c "user root" -c "password $HASH" -c "exit" -c "commit" \
  2>&1 | sed 's/^/  /'

echo
echo "=========== 1. create an account through the VTY ==========="
KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKfakekeyforlabuseonly0000000000000 noc@lab"
CREATE=$($HX -c "configure" -c "user noc" -c "ssh-key $KEY" -c "exit" \
             -c "commit" 2>&1)
printf '%s\n' "$CREATE" | sed 's/^/  /'
PASSWD_LINE=$(getent passwd noc || true)
AK=$(cat /home/noc/.ssh/authorized_keys 2>/dev/null || true)
AKMODE=$(stat -c %a /home/noc/.ssh/authorized_keys 2>/dev/null || echo none)
echo "  passwd entry: ${PASSWD_LINE:-<none>}"
echo "  authorized_keys mode: $AKMODE"

echo
echo "=========== 2. it survives a round trip through the VTY ==========="
RUN=$($HX -c "show running-config" 2>&1)
printf '%s\n' "$RUN" | grep -E "^(ssh |user | ssh-key| password)" | sed 's/^/  /'

echo
echo "=========== 3. the lockout, produced rather than reasoned about ==========="
# Remove the only account with a key AND stop root logging in. Nobody is left.
LOCK=$($HX -c "configure" -c "no user noc" -c "ssh permit-root-login no" \
           -c "commit check" 2>&1)
printf '%s\n' "$LOCK" | sed 's/^/  /'
STILL=$(getent passwd noc || true)

echo
echo "=========== 4a. a value this VTY refuses on its own ==========="
BADP=$($HX -c "configure" -c "ssh port 99999" -c "commit" 2>&1)
printf '%s\n' "$BADP" | sed 's/^/  /'

echo
echo "=========== 4b. a listen address that is not an address ==========="
# The VTY refuses it itself, where it is typed. Until 0.110.0 this section
# said listen-address was free text "on purpose" and asserted that sshd was
# the one to say no; #413 made the VTY read it as an address, the value never
# reached sshd again, and this lab asserted a refusal nobody was making from
# then on - red for every run of the suite, and a Z release runs none (#434).
# What still matters is the other half, and it is kept: the refused commit
# leaves sshd with a configuration it would start with.
BADA=$($HX -c "configure" -c "ssh listen-address not:an:address" \
           -c "commit" 2>&1)
printf '%s\n' "$BADA" | sed 's/^/  /'
# What is in EFFECT afterwards, not what the file looks like: a refused commit
# rolls back by rewriting the file with the values that were running, so the
# bytes legitimately differ while the configuration does not.
#
# The WHOLE answer, stderr and all, and the assertions below are made against
# it rather than against the two lines pulled out of it for the report.
#
# This read `sshd -T 2>/dev/null | grep -i "^listenaddress"` and the negative
# below could not fail in either direction. sshd -T refuses a file it cannot
# parse instead of printing a partial one, so on the box this section is about
# - the one that left `ListenAddress not:an:address` in the file - it exits
# 255, says "bad addr or host: not:an:address (Name or service not known)" on
# STDERR, and prints no configuration at all. 2>/dev/null threw the refusal
# away, the grep had no listenaddress line to select, EFFECTIVE was EMPTY, and
# "not:an:address is not in it" was green. On a box that rolled the address
# back EFFECTIVE holds "listenaddress [::]:22 listenaddress 0.0.0.0:22" and it
# was green again. Driven on 2026-09-15 against OpenSSH 9.2 over two real
# sshd_config files, with this line copied out verbatim: both boxes read
# "[ ok ]".
#
# Keeping stderr puts the refusal itself in the haystack, which is where the
# address the box must not be using appears. The positive beside it is what
# stops the same thing happening again for a different reason: if sshd ever
# stops answering, or rewords its refusal, the haystack goes quiet and the
# check below would be vacuous again - so something has to say the answer is
# there, which is what lab/routing.sh's block_of pair says for the same reason.
SSHD_SAID=$(sshd -T 2>&1)
EFFECTIVE=$(printf '%s\n' "$SSHD_SAID" | grep -i "^listenaddress" | head -2 \
            | tr "\n" " ")
# Directly: is the daemon's configuration still one it would start with?
# That is what "put back" has to mean; a port read out of sshd -T is a proxy
# for it, and a proxy that can be empty for its own reasons.
STILL_VALID=$(sshd -t 2>&1 && echo VALID)
echo "  listen address in effect afterwards: ${EFFECTIVE:-<none>}"
echo "  sshd -t after the refused commit: ${STILL_VALID:-REFUSED}"

echo
echo "=========== 5. secrets ==========="
$HX -c "save" >/dev/null 2>&1
CFGMODE=$(stat -c %a "$PRISMA_CONFIG" 2>/dev/null || echo none)
RBMODE=$(stat -c %a /etc/prisma-routing/rollback/0.conf 2>/dev/null || echo none)
ONDISK=$(grep -c '^ password \$' "$PRISMA_CONFIG" 2>/dev/null || echo 0)
echo "  prisma-routing.conf mode $CFGMODE, rollback revision mode $RBMODE"
echo "  password hashes stored in the file: $ONDISK"

echo
echo "=========== 6. ssh listen-address: a list, v4 and v6 (#593) ==========="
# The box's addresses: two of each family on one interface, so a listener
# on one address is told from one on all of them.
ip link add d593 type dummy 2>/dev/null
ip link set d593 up
for a in 10.59.3.1/24 10.59.3.2/24; do ip addr add $a dev d593; done
for a in 2001:db8:593::1/64 2001:db8:593::2/64; do
  ip -6 addr add $a dev d593 nodad
done
# systemd's `reload ssh` is a SIGHUP to sshd, which re-executes itself on
# the configuration as it now stands: the lab's sshd, the same way.
cat > /usr/local/sbin/systemctl <<'UNIT'
#!/bin/sh
case "$*" in
  "reload ssh") kill -HUP "$(cat /run/sshd-593.pid)" && sleep 1; exit $? ;;
esac
[ -x /usr/bin/systemctl ] && exec /usr/bin/systemctl "$@"
exit 127
UNIT
chmod +x /usr/local/sbin/systemctl
/usr/sbin/sshd -D -e > /tmp/sshd-593.log 2>&1 &
echo $! > /run/sshd-593.pid
sleep 1
banner() {  # <address>: what sshd says on port 22 there, or why not
  timeout 5 python3 -c "
import socket
a = '$1'
s = socket.socket(socket.AF_INET6 if ':' in a else socket.AF_INET)
s.settimeout(3)
s.connect((a, 22))
print(s.recv(64).decode().strip())" 2>&1 | tail -1; }
listening() { sshd -T 2>&1 | grep -i '^listenaddress' | tr '\n' ' '; }
dropin() { grep '^ListenAddress' /etc/ssh/sshd_config.d/prisma-routing.conf \
           2>/dev/null | tr '\n' ' '; }
ssh_line() { $HX -c "show running-config" 2>&1 | grep '^ssh listen-address'; }
commit593() {  # <statement>: committed, and the commit confirmed it arms
  $HX -c "configure" -c "$1" -c "commit" 2>&1
  $HX -c "configure" -c "commit" >/dev/null 2>&1
}
said() {
  echo "  ListenAddress in the drop-in: ${DROP:-<none>}"
  echo "  sshd -T: ${LISTENING:-<none>}"
  echo "  show running-config: ${LINE:-<none>}"
}

echo "--- 6a. two addresses, one of each family"
C6A=$(commit593 "ssh listen-address 10.59.3.1 2001:db8:593::1")
printf '%s\n' "$C6A" | tail -3 | sed 's/^/  /'
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6A_V4=$(banner 10.59.3.1); B6A_V6=$(banner 2001:db8:593::1)
B6A_OTHER=$(banner 10.59.3.2)
echo "  10.59.3.1: $B6A_V4 | 2001:db8:593::1: $B6A_V6 | 10.59.3.2: $B6A_OTHER"
expect_not_in "6a: the list commits" "error" "$C6A"
expect_in "6a: the drop-in names the IPv4 address" "ListenAddress 10.59.3.1" "$DROP"
expect_in "6a: and the IPv6 one, bracketed" \
  "ListenAddress [2001:db8:593::1]" "$DROP"
expect_in "6a: sshd -T reads back both" \
  "listenaddress 10.59.3.1:22 listenaddress [2001:db8:593::1]:22" "$LISTENING"
expect_in "6a: show running-config says the list" \
  "ssh listen-address 10.59.3.1 [2001:db8:593::1]" "$LINE"
expect_in "6a: SSH answers on the IPv4 address" "SSH-2.0-OpenSSH" "$B6A_V4"
expect_in "6a: SSH answers on the IPv6 address" "SSH-2.0-OpenSSH" "$B6A_V6"
expect_not_in "6a: and not on an address it does not name" "SSH-2.0" \
  "$B6A_OTHER"

echo "--- 6b. one IPv6 address"
C6B=$(commit593 "ssh listen-address 2001:db8:593::2")
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6B_V6=$(banner 2001:db8:593::2); B6B_V4=$(banner 10.59.3.1)
echo "  2001:db8:593::2: $B6B_V6 | 10.59.3.1: $B6B_V4"
expect "6b: one ListenAddress" test "$DROP" = "ListenAddress [2001:db8:593::2] "
expect_in "6b: show running-config says it" \
  "ssh listen-address [2001:db8:593::2]" "$LINE"
expect_in "6b: SSH answers on it" "SSH-2.0-OpenSSH" "$B6B_V6"
expect_not_in "6b: and not on IPv4" "SSH-2.0" "$B6B_V4"

echo "--- 6c. one IPv4 address"
C6C=$(commit593 "ssh listen-address 10.59.3.2")
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6C_V4=$(banner 10.59.3.2); B6C_V6=$(banner 2001:db8:593::1)
echo "  10.59.3.2: $B6C_V4 | 2001:db8:593::1: $B6C_V6"
expect "6c: one ListenAddress" test "$DROP" = "ListenAddress 10.59.3.2 "
expect_in "6c: SSH answers on it" "SSH-2.0-OpenSSH" "$B6C_V4"
expect_not_in "6c: and not on IPv6" "SSH-2.0" "$B6C_V6"

echo "--- 6d. no setting: 'no ssh listen-address'"
C6D=$(commit593 "no ssh listen-address")
printf '%s\n' "$C6D" | tail -2 | sed 's/^/  /'
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6D_V4=$(banner 10.59.3.1); B6D_V6=$(banner 2001:db8:593::2)
echo "  10.59.3.1: $B6D_V4 | 2001:db8:593::2: $B6D_V6"
expect "6d: the drop-in names no ListenAddress, sshd's default" test -z "$DROP"
expect_in "6d: sshd -T: every IPv6 address" "listenaddress [::]:22" "$LISTENING"
expect_in "6d: and every IPv4 one" "listenaddress 0.0.0.0:22" "$LISTENING"
expect_in "6d: show running-config says both families" \
  "ssh listen-address 0.0.0.0 [::]" "$LINE"
expect_in "6d: SSH answers on IPv4" "SSH-2.0-OpenSSH" "$B6D_V4"
expect_in "6d: SSH answers on IPv6" "SSH-2.0-OpenSSH" "$B6D_V6"

echo "--- 6e. no setting: a saved file without the line, loaded"
commit593 "ssh listen-address 10.59.3.1" >/dev/null
$HX -c "save" >/dev/null 2>&1
sed -i '/^ssh listen-address/d' "$PRISMA_CONFIG"
L6E=$($HX --load "$PRISMA_CONFIG" 2>&1)
printf '%s\n' "$L6E" | tail -2 | sed 's/^/  /'
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6E_V4=$(banner 10.59.3.2); B6E_V6=$(banner 2001:db8:593::1)
echo "  10.59.3.2: $B6E_V4 | 2001:db8:593::1: $B6E_V6"
expect "6e: the drop-in names no ListenAddress" test -z "$DROP"
expect_in "6e: SSH answers on IPv4" "SSH-2.0-OpenSSH" "$B6E_V4"
expect_in "6e: SSH answers on IPv6" "SSH-2.0-OpenSSH" "$B6E_V6"

echo "--- 6f. a file saved before #593: 'ssh listen-address [::]', loaded"
# What every box that never had an ssh commit saved: the first address of
# sshd's default, read alone. Loaded as written it took IPv4's SSH away.
$HX -c "save" >/dev/null 2>&1
sed -i 's/^ssh listen-address .*/ssh listen-address [::]/' "$PRISMA_CONFIG"
grep '^ssh listen-address' "$PRISMA_CONFIG" | sed 's/^/  saved: /'
L6F=$($HX --load "$PRISMA_CONFIG" 2>&1)
DROP=$(dropin); LISTENING=$(listening); LINE=$(ssh_line); said
B6F_V4=$(banner 10.59.3.1); B6F_V6=$(banner 2001:db8:593::1)
echo "  10.59.3.1: $B6F_V4 | 2001:db8:593::1: $B6F_V6"
expect_in "6f: SSH still answers on IPv4" "SSH-2.0-OpenSSH" "$B6F_V4"
expect_in "6f: and on IPv6" "SSH-2.0-OpenSSH" "$B6F_V6"

echo "--- 6g. an address the box does not have"
C6G=$($HX -c "configure" -c "ssh listen-address 10.59.3.1 192.0.2.99" \
          -c "commit" 2>&1)
printf '%s\n' "$C6G" | sed 's/^/  /'
DROP=$(dropin)
B6G_V6=$(banner 2001:db8:593::1)
expect_in "6g: refused at commit, naming it" \
  "192.0.2.99: not an address of this box" "$C6G"
expect_not_in "6g: nothing committed" "commit complete" "$C6G"
expect "6g: and sshd keeps what it had" test -z "$DROP"
expect_in "6g: SSH over IPv6 still answers" "SSH-2.0-OpenSSH" "$B6G_V6"
kill "$(cat /run/sshd-593.pid)" 2>/dev/null

echo
echo "=========== VERDICT ==========="
expect_in "the account exists as a system account" "noc:" "${PASSWD_LINE:-}"
expect_in "with the VTY as its login shell" "/usr/bin/prismash-login" \
  "${PASSWD_LINE:-}"
expect_in "and the key reached authorized_keys" "AAAAC3NzaC1lZDI1NTE5" "${AK:-}"
expect "which is readable only by its owner" test "$AKMODE" = "600"
expect_in "a change to accounts is applied as 'commit confirmed'" \
  "commit confirmed" "$CREATE"
expect_in "and says why, rather than choosing it silently" \
  "cut off the session" "$CREATE"
expect_in "the running configuration reports the account" "user noc" "$RUN"
expect_in "and the key" "ssh-key ssh-ed25519" "$RUN"

expect_in "a change that strands everybody is refused" "locks everybody out" \
  "$LOCK"
expect_in "and says what to do about it" "authorized key" "$LOCK"
expect "the refused change was not applied: the account is still there" \
  test -n "${STILL:-}"

expect_in "the VTY refuses an impossible port itself" "expected 1-65535" "$BADP"
expect_in "the VTY refuses a listen address that is not one" \
  "not an IP address" "$BADA"
expect_not_in "and commits nothing on its account" "commit complete" "$BADA"
expect_in "sshd could be asked what it would run with at all" \
  "listenaddress" "$SSHD_SAID"
expect_not_in "and the address it refused is not in effect" \
  "not:an:address" "$SSHD_SAID"
expect_in "so sshd would still start" "VALID" "${STILL_VALID:-}"

# Not "readable only by root", which is what this asserted while the VTY
# chmod'd 0600 over the mode the installer states: an account holding a role
# has to read the saved configuration to build its candidate and write it to
# save one, and every rollback revision with it. What must never happen is the
# other direction - this file carries the password hash of every account on the
# box, so world-readable is an offline cracking target handed to every login.
expect "the saved configuration is writable by the role group" \
  test "$CFGMODE" = "660"
expect "and so is every rollback revision" test "$RBMODE" = "660"
expect "and neither is readable by everybody" \
  sh -c "[ \"$(printf %s "$CFGMODE" | tail -c 1)\" = 0 ] && [ \"$(printf %s "$RBMODE" | tail -c 1)\" = 0 ]"
expect "the hash IS stored, or a rollback could not restore a password" \
  test "${ONDISK:-0}" -ge 1
expect_not_in "but 'show running-config' does not print it" "\$6\$" "$RUN"
expect_in "it says the password is set instead" "not shown" "$RUN"
verdict
