#!/bin/sh
# The boot self-test's account check: a role the box grants and nobody can use.
#
# The check it replaces made the condition it was checking for. It ran the API
# call under "setpriv --groups vpp", which synthesises the membership, so it
# answered "would an account in group vpp work" - always yes - and never "is
# any account in group vpp", which for the whole life of the product was no
# (#184). A green self-test therefore coexisted with every operator account
# being inert.
#
# So these cases are about the box's OWN accounts. The self-test is driven with
# a stubbed getent/id/runuser: what the accounts are is exactly the variable
# under test, and there is no way to arrange four different populations of real
# accounts on the machine running this.
set -u
fail=0
ok()  { echo "  [ ok ] $*"; }
bad() { echo "  FAIL: $*"; fail=$((fail+1)); }

REPO=$(cd "$(dirname "$0")/.." && pwd)
ST="$REPO/appliance/selftest"
BIN=$(mktemp -d)
FAKE=$(mktemp -d)
export FAKE
trap 'rm -rf "$BIN" "$FAKE"' EXIT

cat > "$BIN/getent" <<'EOF'
#!/bin/sh
case "${1:-}" in
  passwd) cat "$FAKE/passwd" ;;
  group)  if [ -n "${2:-}" ]; then grep "^$2:" "$FAKE/group"; else cat "$FAKE/group"; fi ;;
  *) exit 2 ;;
esac
EOF
# id -nG <name>: the account's groups, from the fake box.
cat > "$BIN/id" <<'EOF'
#!/bin/sh
for a in "$@"; do name=$a; done
sed -n "s/^$name  *//p" "$FAKE/groups"
EOF
# The account the self-test chose to prove the reach with, and the exit status
# the API call gives it.
cat > "$BIN/runuser" <<'EOF'
#!/bin/sh
echo "${2:-}" >> "$FAKE/runuser.log"
exit "$(cat "$FAKE/api_rc" 2>/dev/null || echo 0)"
EOF
# Present so that using it is visible rather than impossible: an assertion that
# passes because a command is missing would pass on the old self-test too.
cat > "$BIN/setpriv" <<'EOF'
#!/bin/sh
echo "$*" >> "$FAKE/setpriv.log"
exit 0
EOF
cat > "$BIN/vat2" <<'EOF'
#!/bin/sh
echo bpa_pool_add_del
EOF
chmod +x "$BIN"/*

# Everything else the self-test looks at is missing on purpose: those checks
# fail, say so, and are not what this file is about. Only the account lines are
# read back.
said() {  # <extended regexp> - does a line of the last run match it?
  printf '%s\n' "$OUTPUT" | grep -qE "$1"
}

run() {  # -> $OUTPUT
  rm -f "$FAKE/runuser.log" "$FAKE/setpriv.log"
  printf 'vpp:x:900:\nfrr:x:901:\nfrrvty:x:902:\nhexa-admin:x:903:\nhexa-cgnat:x:904:\n' \
    > "$FAKE/group"
  [ -f "$FAKE/api_rc" ] || echo 0 > "$FAKE/api_rc"
  OUTPUT=$(PATH="$BIN:$PATH" HEXA_SELFTEST_OUT=/dev/stdout sh "$ST" 2>/dev/null)
}


echo "=== an account with a role and not the memberships ==="
# The defect itself: the VTY granted the rank and put the account in no group,
# so every command it issues comes back "permission denied" and reads as an
# authorization refusal.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\nnoc:x:1000:1000::/home/noc:/usr/bin/hexash-login\n' \
  > "$FAKE/passwd"
printf 'noc hexa-admin\n' > "$FAKE/groups"
run
if said '\[FAIL\] noc has role administrator and is in none of:'; then
  ok "it is reported as a failed check, with the rank the box grants it"
else
  bad "the account with an unusable role was not reported: $(printf '%s\n' "$OUTPUT" | grep noc || echo '<nothing about it>')"
fi
# The groups by name: "something is missing" sends the reader nowhere.
if said '\[FAIL\] noc .*none of: vpp frr frrvty'; then
  ok "and it names every membership the role needs"
else
  bad "it did not name the missing memberships"
fi
# The property the old check could not have. With no account in the group the
# reach cannot be proven, and saying so is the whole point.
if said 'no account on this box is in group vpp'; then
  ok "and the reach is reported as unproven"
else
  bad "it claimed the API is usable with no account able to use it"
fi
if [ -s "$FAKE/setpriv.log" ]; then
  bad "it synthesised a group to prove reach: $(cat "$FAKE/setpriv.log")"
else
  ok "setpriv was not used to manufacture the membership"
fi

echo "=== an account whose role the box can actually honour ==="
printf 'noc hexa-admin hexa-cgnat vpp frr frrvty\n' > "$FAKE/groups"
run
if said 'noc .*is in none of'; then
  bad "a complete account was reported as broken"
else
  ok "nothing is reported against it"
fi
if said 'the binary API works for noc'; then
  ok "and the reach is proven by that very account"
else
  bad "the reach was not proven by the real account"
fi
[ "$(cat "$FAKE/runuser.log" 2>/dev/null)" = noc ] \
  && ok "the call really ran as noc" \
  || bad "the API call was not made as the account: $(cat "$FAKE/runuser.log" 2>/dev/null)"

echo "=== a read-only account missing the same groups ==="
# It cannot read either, which is worth saying - but it is not a role that the
# box grants and refuses, so it must not be a boot failure. A self-test that
# fails a box for an account nobody gave anything to is one whose failures get
# ignored.
printf 'noc \n' > "$FAKE/groups"
run
if said '\[warn\] noc is read-only and is in none of: vpp frr frrvty'; then
  ok "said as a warning, with the groups named"
else
  bad "the read-only account was not mentioned at all"
fi
if said '\[FAIL\] noc'; then
  bad "and it was counted as a failed check"
else
  ok "and not counted as a failed check"
fi

echo "=== a box with no operator accounts yet ==="
# The state every freshly installed box is in. It must not fail: the operator
# has not made an account yet, and a red self-test on first boot is one nobody
# reads twice.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\ndaemon:x:1:1::/:/usr/sbin/nologin\n' \
  > "$FAKE/passwd"
printf '\n' > "$FAKE/groups"
run
if said 'no operator account exists yet'; then
  ok "said in as many words"
else
  bad "a fresh box says nothing about having no accounts"
fi
if said '\[FAIL\].*account'; then
  bad "and a fresh box was failed over it"
else
  ok "and it is not a failure"
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
