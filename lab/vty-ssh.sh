#!/bin/sh
# The SSH daemon and the accounts that may log in, from the VTY.
#
# This is the one configuration that can lock the operator out of the box they
# are configuring, from the other side of the country. The assertion that
# matters is therefore not that a user can be created - it is that a change
# which strands everybody is REFUSED, and that is verified by producing the
# lockout rather than by reasoning about it.
set -u
. /src/lab/lib.sh   # startup_conf, start_frr, use_binary_api
. /src/lab/verdict.sh
mkdir -p /run/vpp /vpp_plugins /var/log/vpp /etc/prisma-routing /etc/ssh/sshd_config.d
(cd /src/.build-vpp && ninja >/dev/null 2>&1) || {
  mkdir -p /src/.build-vpp && cd /src/.build-vpp && cmake -G Ninja /src >/dev/null && ninja
}
cd /src
HX="python3 /src/src/prismash/prismash.py"
export PRISMA_CONFIG=/etc/prisma-routing/prisma-routing.conf
# The VTY marks its accounts by their login shell, so it has to exist.
install -D -m 0755 /src/appliance/prismash-login /usr/bin/prismash-login
ssh-keygen -A >/dev/null 2>&1

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
echo "=========== 4b. a value only sshd can refuse ==========="
# listen-address is a free-form string here on purpose: this VTY has no
# business re-implementing sshd's address grammar. So the daemon is the thing
# that says no - and it MUST say so before it is reloaded, because a syntax
# error that stops sshd starting is the same lockout by another route.
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
expect_in "sshd refuses what the VTY let through" "sshd refused" "$BADA"
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
