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
# systemd, answered from the fake box rather than from this one. Stubbed whole
# rather than passed through: the machine running these tests may itself have a
# vpp.service, and a test whose answer depends on that is a test that reads
# differently on the developer's box and on the build runner.
cat > "$BIN/systemctl" <<'EOF'
#!/bin/sh
case "$*" in
  *"-p MainPID"*) cat "$FAKE/mainpid" 2>/dev/null || echo 0 ;;
  *"-p NRestarts"*) echo 0 ;;
  "is-failed "*) echo inactive ;;
  *) : ;;
esac
exit 0
EOF
# The one file that says whether the drop-in took effect. The self-test reads it
# with cat, so cat is where the fake box answers - everything else goes through
# to the real one.
cat > "$BIN/cat" <<'EOF'
#!/bin/sh
case "${1:-}" in
  /proc/*/oom_score_adj)
    if [ -f "$FAKE/oom_score_adj" ]; then exec /bin/cat "$FAKE/oom_score_adj"; fi
    exec /bin/cat "$1" ;;
  *) exec /bin/cat "$@" ;;
esac
EOF
# A dataplane that answers, when a case needs one. Absent from $FAKE it fails,
# which is what a box with no VPP looks like.
cat > "$BIN/vppctl" <<'EOF'
#!/bin/sh
[ -f "$FAKE/vppctl_ok" ] || exit 1
exit 0
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

echo "=== the dataplane's place in the OOM killer's selection ==="
# The other half of #172. The drop-in on vpp.service is one line, and nothing
# on an installed box ever checked that the line arrived: a drop-in that was
# never installed and one that is working look the same from outside. What the
# self-test reads is /proc/<vpp pid>/oom_score_adj, which is what the kernel
# acts on.
#
# The self-test must never write that file. These cases therefore arrange it
# from outside, as the drop-in would, and the self-test only reads.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\n' > "$FAKE/passwd"
printf '\n' > "$FAKE/groups"

echo 4242 > "$FAKE/mainpid"
echo -1000 > "$FAKE/oom_score_adj"
run
if said '\[ ok \] dataplane out of the OOM killer.s selection'; then
  ok "a protected dataplane is reported as protected"
else
  bad "a box with the drop-in in effect said nothing about it"
fi

echo 0 > "$FAKE/oom_score_adj"
run
if said "\[FAIL\] vpp's oom_score_adj is 0"; then
  ok "and a box where it did not take effect is a FAILED check"
else
  bad "a dataplane the kernel may choose was not reported: $(printf '%s\n' "$OUTPUT" | grep -i oom_score || echo '<nothing>')"
fi
# Not "something is wrong". Whoever is at the console has to be able to act.
if said 'hexarouting-oom-protect.conf' && said 'daemon-reload'; then
  ok "and it names the drop-in and what to do about it"
else
  bad "the failure does not say which file or what to do"
fi

echo -500 > "$FAKE/oom_score_adj"
run
if said '\[warn\] vpp.s oom_score_adj is -500: lowered, not removed'; then
  ok "a merely lowered score is reported as still selectable"
else
  bad "a lowered-but-not-removed score was not distinguished from -1000"
fi
if said "\[FAIL\].*oom_score_adj"; then
  bad "and it was counted as a failed check, which -1000 and -500 are not"
else
  ok "and not as a failed check: it is a weaker setting, not a missing one"
fi

# A dataplane somebody started by hand. The unit owns no process, so no drop-in
# on that unit covers what is running - and the box must say so rather than
# stay quiet, because "no line" reads as "nothing to report".
rm -f "$FAKE/oom_score_adj"
echo 0 > "$FAKE/mainpid"
: > "$FAKE/vppctl_ok"
run
if said 'VPP is running but vpp.service owns no process'; then
  ok "VPP outside its unit is reported, since the drop-in cannot reach it"
else
  bad "a hand-started VPP was passed over in silence"
fi
rm -f "$FAKE/vppctl_ok"

# Unknown is not protected. If the file cannot be read the box must say the
# question is open, not omit the line.
echo 999999 > "$FAKE/mainpid"
run
if said "\[FAIL\] cannot read /proc/999999/oom_score_adj"; then
  ok "an unreadable oom_score_adj is a failure, not a silence"
else
  bad "the self-test could not read the file and said nothing"
fi
rm -f "$FAKE/mainpid"

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
