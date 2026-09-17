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
# Where a path is mounted from. The records check compares the source of
# /var/log/hexarouting with the source of /, and on the machine running
# these tests both are the same disk - which is the WARNING case, so
# without this stub the passing branch could never be reached.
cat > "$BIN/findmnt" <<'EOF'
#!/bin/sh
for a in "$@"; do t=$a; done
case "$t" in
  /) cat "$FAKE/mnt_root" 2>/dev/null || echo /dev/vda2 ;;
  *) cat "$FAKE/mnt_records" 2>/dev/null ;;
esac
EOF
# NOT a stub: the real tuner, reached through $BIN so that HEXA_TUNE can be
# pointed elsewhere for the two boxes that need it to be missing or mute. The
# self-test asks it what tuning is in effect and renders the answer, and a
# stub here would mean the shape of that answer was agreed between two files
# with nothing comparing them - which is the defect the whole check is about.
ln -sf "$REPO/appliance/hexarouting-tune" "$BIN/hexarouting-tune"

# Only the free-space line matters to the check, and only that it is there.
cat > "$BIN/df" <<'EOF'
#!/bin/sh
echo "Filesystem Size Used Avail Use% Mounted"
echo "/dev/vdb1 40G 1G 37G 3% /var/log/hexarouting"
EOF
# From the fake box, not a constant, and for the third time in this file:
# `echo bpa_pool_add_del` here made "binary API NOT registered: no CGNAT pool
# can be configured here" unreachable by every box this suite arranges. That is
# the check written for #119 - the medium shipped without the vat2 plugin for
# the life of the project, and such a box routes, answers, and fails every
# pool commit - so it is the one you most want to be able to go red.
#
# The systemctl stub below carries the same repair twice already (#222). Fixing
# these one at a time is what produced a third, which is why this commit also
# adds the reader that refuses a check no box has seen fail (#335).
cat > "$BIN/vat2" <<'EOF'
#!/bin/sh
cat "$FAKE/dump_apis" 2>/dev/null || echo bpa_pool_add_del
EOF
# The journal, answered from the fake box for the same reason systemctl below
# is. This was NOT stubbed, and it is the one command the self-test reaches for
# that still came from the machine running these tests: swept for with a PATH
# of loggers, `journalctl -u vpp.service --no-pager -n 20` and `journalctl -u
# hexarouting.service -n 6 --no-pager -o cat` both went to this box's own
# systemd. On a developer box that really does run vpp.service, that box's
# journal is spliced into the report - under a [FAIL] this suite arranged, and
# into the same text the 80-column check at the bottom reads. A journal line is
# arbitrarily long, so the suite is green on a build runner with no such unit
# and red on the appliance itself, over a product nobody changed.
#
# Empty by default, because that is what a box with no journal for the unit
# answers, and every box here but the two that arrange one is that box.
cat > "$BIN/journalctl" <<'EOF'
#!/bin/sh
case "$*" in
  *vpp.service*)          cat "$FAKE/journal_vpp" 2>/dev/null ;;
  *hexarouting.service*)  cat "$FAKE/journal_hexarouting" 2>/dev/null ;;
  *) : ;;
esac
exit 0
EOF
# systemd, answered from the fake box rather than from this one. Stubbed whole
# rather than passed through: the machine running these tests may itself have a
# vpp.service, and a test whose answer depends on that is a test that reads
# differently on the developer's box and on the build runner.
cat > "$BIN/systemctl" <<'EOF'
#!/bin/sh
case "$*" in
  *"-p MainPID"*) cat "$FAKE/mainpid" 2>/dev/null || echo 0 ;;
  # From the fake box, not a constant. It was `echo 0` here, which made the
  # two branches that matter - "restarted N time(s)" and "has restarted N
  # times: it is looping" - unreachable by anything, while the check they
  # belong to is the one that tells a service in a loop from a healthy one
  # (#222). A service that is restarting is "active" about half the time
  # somebody looks at it.
  *"-p NRestarts"*) cat "$FAKE/nrestarts" 2>/dev/null || echo 0 ;;
  # From the fake box. It was the constant `inactive`, so the branch that
  # reports the CGNAT configuration as not applied had never run (#222).
  "is-failed "*) cat "$FAKE/is_failed" 2>/dev/null || echo inactive ;;
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
# stat, for the ssh host keys only; everything else goes through to the real
# one. The host-key check compares a key's mtime with the moment its file was
# created on the filesystem, so the filesystem the tmpdir happens to be on -
# one that records a birth time, or one that does not - would decide these
# cases instead of the fixture, and differently on a developer box and on the
# build runner. Same reason systemctl is stubbed whole rather than passed
# through. A key with no line in the fixture is one stat could not answer for.
cat > "$BIN/stat" <<'EOF'
#!/bin/sh
for a in "$@"; do p=$a; done
case "$p" in
  */etc/ssh/ssh_host_*_key)
    if [ -f "$FAKE/keytimes" ]; then
      sed -n "s|^${p##*/} ||p" "$FAKE/keytimes"
      exit 0
    fi ;;
esac
exec /usr/bin/stat "$@"
EOF
cat > "$BIN/vppctl" <<'EOF'
#!/bin/sh
[ -f "$FAKE/vppctl_ok" ] || exit 1
# What the dataplane HAS, which is half of every comparison the self-test
# makes: the other half is the file the box was loaded from. Both come from
# the fake box, because the whole question is what happens when they disagree.
case "$*" in
  "show bpa logging") cat "$FAKE/bpa_logging" 2>/dev/null ;;
  "show bpa config")  cat "$FAKE/bpa_config" 2>/dev/null ;;
  # The two listings the pair check reads. Absent from $FAKE they are empty,
  # which is what a dataplane holding nothing but local0 looks like.
  "show interface")   cat "$FAKE/show_interface" 2>/dev/null ;;
  # The addresses, read separately from the interface list because the
  # dataplane reports them separately and the self-test reads them that way.
  "show interface addr") cat "$FAKE/show_interface_addr" 2>/dev/null ;;
  "show lcp")         cat "$FAKE/show_lcp" 2>/dev/null ;;
  # The plugin listing. Empty in $FAKE is a dataplane that came up without
  # the CGNAT plugin, which is a box that forwards nothing and fails every
  # CGNAT command while answering "show version" perfectly well.
  "show plugins")     cat "$FAKE/show_plugins" 2>/dev/null ;;
  # The statistics segment's own heap. VPP answers this with a named counter
  # and `verbose`, and how full it is is the only thing between the box and an
  # os_panic, so it is the variable under test rather than a fixture the
  # harness keeps constant. Absent from $FAKE it answers nothing, which is a
  # dataplane that did not report it - a state of its own.
  "show statistics segment"*) cat "$FAKE/statseg" 2>/dev/null ;;
  # What the box ended up carrying. The last line of the report is the one
  # an operator looks at to see whether the thing they configured is
  # there at all.
  "show bpa pool")    cat "$FAKE/bpa_pools" 2>/dev/null ;;
  # The uRPF listing. Absent from $FAKE it is empty, which the self-test reads
  # the same way as "no interface has uRPF enabled" - both mean the dataplane
  # has none.
  "show bpa urpf")    cat "$FAKE/bpa_urpf" 2>/dev/null ;;
esac
exit 0
EOF
# The kernel's account of one interface, and of the default route. Stubbed for
# the reason findmnt and systemctl are: what a card is carrying is exactly the
# variable under test in the management-network cases (#303), and the machine
# running these tests cannot be made to answer differently about its own.
cat > "$BIN/ip" <<'EOF'
#!/bin/sh
case "$*" in
  *"addr show dev "*)
    # "-o -4 addr show dev ens18" -> ens18, which is the fixture's name.
    cat "$FAKE/ip_addr_${*##* }" 2>/dev/null ;;
  *"route list default") cat "$FAKE/ip_default" 2>/dev/null ;;
esac
exit 0
EOF
# FRR, and only when a case asks for it. Left failing by default because that
# is what the machine running these tests looks like, and because the pair
# check reads the running configuration to decide how loudly to speak.
cat > "$BIN/vtysh" <<'EOF'
#!/bin/sh
[ -f "$FAKE/frr_running" ] || exit 1
case "$*" in
  *"show running-config"*) cat "$FAKE/frr_running" ;;
  # The daemon list a case can override, because "which daemons are up" is
  # itself something the self-test has to get right. The default is a box the
  # installer has finished with: every routing daemon the product claims,
  # ospf6d included (#187).
  *"show daemons"*)
    if [ -f "$FAKE/frr_daemons" ]; then cat "$FAKE/frr_daemons"
    else echo "bgpd ospfd ospf6d isisd zebra staticd mgmtd"; fi ;;
  # RPKI answers in two ways and the difference is the whole check: a
  # module that is NOT loaded makes vtysh exit non-zero on this command,
  # and one that is loaded answers, with or without a connected cache. So
  # the absence of the file is the unloaded case rather than a missing
  # fixture.
  *"show rpki cache-connection"*)
    [ -f "$FAKE/rpki" ] || exit 1
    cat "$FAKE/rpki" ;;
  *) : ;;
esac
exit 0
EOF
chmod +x "$BIN"/*

# Everything else the self-test looks at is missing on purpose: those checks
# fail, say so, and are not what this file is about. Only the account lines are
# read back.
said() {  # <extended regexp> - does a line of the last run match it?
  printf '%s\n' "$OUTPUT" | grep -qE "$1"
}

said_across() {  # <extended regexp> - does the run say it, wherever it folds?
  # said() over the output with its line breaks collapsed.
  #
  # The self-test is read on an 80-column console, so a message carrying an
  # interface name, a PCI address or a 31-cpu list puts the value on a row of
  # its own (#285). A needle written when the message was one line then
  # straddles the break and stops matching - over a box saying exactly the
  # right thing. That is the same mechanism as #279, which cost three needles
  # in the labs, and it is worse here: half of these assertions are the ones
  # that check a FAILURE is reported.
  #
  # said() stays for anything that must be on ONE line, which is what the
  # width check itself is about.
  printf '%s\n' "$OUTPUT" | tr -s '[:space:]' ' ' | grep -qE "$1"
}

# Every run's exit status, accumulated here and judged once at the end.
#
# docs/appliance.md says of the self-test that it "always exits zero: the
# diagnostic MUST NOT bring down the boot", and nothing read the status. The
# script could be made to report its own verdict as one - exit "$fail" instead
# of exit 0 - and all 34 suites stayed green, while the boot path of a racked
# appliance quietly gained a way to fail. Read back the moment this file has
# arranged a box that fails checks, which is most of the cases below.
runs=0
runs_with_fail=0
bad_exits=0
first_bad_exit=""
run() {  # -> $OUTPUT
  rm -f "$FAKE/runuser.log" "$FAKE/setpriv.log"
  # The groups a finished installation has, rewritten every run so no case has
  # to care about them - except the one case that IS about them. That case
  # leaves group.keep behind, because a fixture the harness overwrites is a
  # fixture that measures the harness (#222).
  [ -f "$FAKE/group.keep" ] || \
  printf 'vpp:x:900:\nfrr:x:901:\nfrrvty:x:902:\nhexa-admin:x:903:\nhexa-cgnat:x:904:\n' \
    > "$FAKE/group"
  [ -f "$FAKE/api_rc" ] || echo 0 > "$FAKE/api_rc"
  # A MANIFEST, on every box, for the same reason the groups above are
  # rewritten every run: no case here is about it, and a box without one is
  # warned about (#302) - which would put a warning on all hundred-odd of
  # these, break the two cases that count the warnings on a box, and bury the
  # ones each case arranged under a line none of them asked for.
  #
  # A file of its own rather than a checksum of something already in the fake
  # root, because the roots differ from case to case and a manifest naming a
  # file some of them do not have would report those boxes as carrying a file
  # from another release. usr/share/hexarouting is read by name by the version
  # check and the manifest check and globbed by nothing, so a file put there
  # reaches no other check in this file.
  #
  # What the manifest MEANS is measured in test/test_manifest.sh, which
  # installs a real root with appliance/install.sh and reads it back with this
  # same self-test. This is a fixture, not an assertion.
  #
  # manifest.keep is how the three cases that ARE about the manifest opt out,
  # including the one that needs no file at all - the same arrangement
  # group.keep and mgmt.keep already make, and for the same reason: a fixture
  # the harness overwrites is a fixture that measures the harness (#222).
  if [ ! -f "$FAKE/manifest.keep" ] && \
     [ ! -f "$FAKE/root/usr/share/hexarouting/MANIFEST" ]; then
    mkdir -p "$FAKE/root/usr/share/hexarouting"
    echo "a file of the build this box says it is running" \
      > "$FAKE/root/usr/share/hexarouting/a-file-of-that-build"
    ( cd "$FAKE/root" && sha256sum usr/share/hexarouting/a-file-of-that-build ) \
      > "$FAKE/root/usr/share/hexarouting/MANIFEST"
  fi
  # A management network on every box, for the same reason the MANIFEST above
  # is written on every box: no case here is about it, a box that has none is
  # a FAILED check (#303), and without this every one of the hundred-odd runs
  # would carry that [FAIL] - burying whatever the case actually arranged and
  # breaking the two cases that count what a box reports.
  #
  # mgmt.keep is how the cases that ARE about it opt out, including the one
  # that needs no file at all. A fixture the harness overwrites is a fixture
  # that measures the harness.
  if [ ! -f "$FAKE/mgmt.keep" ]; then
    mkdir -p "$FAKE/root/etc/network/interfaces.d" \
             "$FAKE/root/sys/class/net/ens18"
    printf 'auto ens18\niface ens18 inet dhcp\n' \
      > "$FAKE/root/etc/network/interfaces.d/hexarouting"
    printf '2: ens18    inet 10.0.2.15/24 brd 10.0.2.255 scope global ens18\n' \
      > "$FAKE/ip_addr_ens18"
    printf 'default via 10.0.2.2 dev ens18 proto dhcp metric 100\n' \
      > "$FAKE/ip_default"
  fi
  # The root is always pointed at the fake box, even for the cases that put
  # nothing in it: an absent file there reads the same as the absent one on
  # this machine, and a developer box that really does run VPP would otherwise
  # give these tests a different /etc/vpp/startup.conf than the build runner.
  # Same reason systemctl is stubbed whole rather than passed through.
  # RUN_PATH is what a box MISSING one of the commands the self-test reaches
  # for is arranged with. Everything else here stubs a command to answer
  # differently; this is the only way to arrange one that is not there at all,
  # and the check that says "nothing here could look" is a different branch
  # from the one that says "I looked and it was wrong". Empty is this
  # machine's own PATH, which is every other box in this file.
  # HEXA_TUNE is the self-test's seam onto hexarouting-tune, the one external
  # command that decides what it can say about the tuning in effect. Empty is
  # the symlink in $BIN, which is the REAL tuner against the fake root - so
  # every box below reads its own tuning the way a booted one does. It is set
  # only by the two boxes that have to arrange a tuner which is not there, and
  # one that answers nothing, neither of which can be arranged by a tuner that
  # works.
  OUTPUT=$(PATH="$BIN:${RUN_PATH:-$PATH}" HEXA_SELFTEST_OUT=/dev/stdout \
           HEXA_TUNE="${HEXA_TUNE:-hexarouting-tune}" \
           HEXA_SELFTEST_ROOT="$FAKE/root" sh "$ST" 2>/dev/null)
  rc=$?
  # Every line the self-test has printed, over every box this suite arranges,
  # kept for the 80-column check at the end. A line only goes over when a
  # value is interpolated into it, so reading the source cannot find one.
  printf '%s\n' "$OUTPUT" >> "$FAKE/every-line"
  printf '%s\n' "$OUTPUT" | sed 's/^ *//' | sort -u \
    >> "$FAKE/per-box-lines"
  runs=$((runs + 1))
  printf '%s\n' "$OUTPUT" | grep -q '\[FAIL\]' && runs_with_fail=$((runs_with_fail + 1))
  if [ "$rc" -ne 0 ]; then
    bad_exits=$((bad_exits + 1))
    # The verdict line identifies which box it was refusing to come up for,
    # which is more use than the ordinal of the run.
    [ -n "$first_bad_exit" ] || first_bad_exit="run $runs exited $rc after: $(printf '%s\n' "$OUTPUT" | grep -E 'check\(s\) failed|HexaRouting ready' | tail -1)"
  fi
}

# A fake box's cores: what the kernel is isolating, what VPP was TOLD, and
# where the kernel actually put VPP's threads. Three separate places in the
# fake root, because the whole question the checks answer is what happens when
# they disagree - and a machine running these tests cannot be made to disagree
# with itself about isolcpus without rebooting it.
mkcpus() {  # <isolated> <online> <corelist-workers> <main-core> <worker cpu>...
  R="$FAKE/root"
  rm -rf "$R"
  mkdir -p "$R/sys/devices/system/cpu" "$R/etc/vpp" "$R/proc/4242/task/100"
  echo "$1" > "$R/sys/devices/system/cpu/isolated"
  echo "$2" > "$R/sys/devices/system/cpu/online"
  { echo "# GENERATED by hexarouting-tune"
    echo "cpu {"
    echo "  main-core $4"
    [ -n "$3" ] && echo "  corelist-workers $3"
    echo "}"
  } > "$R/etc/vpp/startup.conf"
  echo vpp_main > "$R/proc/4242/task/100/comm"
  printf 'Cpus_allowed_list:\t%s\n' "$4" > "$R/proc/4242/task/100/status"
  i=101
  shift 4
  for c in "$@"; do
    mkdir -p "$R/proc/4242/task/$i"
    echo "vpp_wk_$((i - 101))" > "$R/proc/4242/task/$i/comm"
    printf 'Cpus_allowed_list:\t%s\n' "$c" > "$R/proc/4242/task/$i/status"
    i=$((i + 1))
  done
  echo 4242 > "$FAKE/mainpid"
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

echo "=== is the allocation log actually leaving the box? (#209) ==="
# The #172 shape, on the setting that carries the connection record: a
# statement the dataplane REFUSED looks, from the configuration, exactly like
# one that is working. So the self-test reads the file the box was loaded from
# and the state the dataplane has, and compares them. It configures nothing.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\n' > "$FAKE/passwd"
printf '\n' > "$FAKE/groups"
: > "$FAKE/vppctl_ok"
export HEXA_CONFIG="$FAKE/hexarouting.conf"

logging() {  # <state> <exporter line body>
  printf 'IPFIX export of block events (RFC 8158)\n  state       %s\n  exporter    %s\n  records     0 emitted, 0 dropped\nTCP transport: disabled\n' \
    "$1" "$2" > "$FAKE/bpa_logging"
}
: > "$FAKE/bpa_config"

echo "--- the box was told to export and the dataplane has it off ---"
printf 'cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739\ncgnat logging enable\n' \
  > "$FAKE/hexarouting.conf"
logging disabled "none: nothing is sent over UDP"
run
if said '\[FAIL\] the saved configuration names an IPFIX collector and the dataplane'; then
  ok "a refused exporter statement is a FAILED check"
else
  bad "the box exported nothing and said nothing: $(printf '%s\n' "$OUTPUT" | grep -i ipfix || echo '<nothing>')"
fi

echo "--- the exporter took and the export state did not ---"
logging disabled "10.0.0.2:4739 src 10.0.0.1 (path mtu 512, template every 20s)"
run
if said '\[FAIL\] the saved configuration turns UDP export ON and the dataplane has it'; then
  ok "an export state that was refused is a FAILED check"
else
  bad "the export state was refused and nothing said so"
fi
# Whoever is at the console has to know what it costs, not only that a line is red.
if said 'connection record'; then
  ok "and it says what the missing log is"
else
  bad "the failure does not say what is not being recorded"
fi

echo "--- enabled, and the exporter cleared underneath it ---"
# Measured: clearing VPP's exporter leaves the export state enabled, so
# "state enabled" on its own is not evidence that anything is being sent.
printf '\n' > "$FAKE/hexarouting.conf"
logging enabled "none: nothing is sent over UDP"
run
if said '\[FAIL\] UDP export is enabled and the IPFIX exporter has no collector'; then
  ok "an enabled export with nowhere to send is a FAILED check"
else
  bad "a box producing records and sending none was reported as fine"
fi

echo "--- exporting, which is the only state that passes ---"
printf 'cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739\ncgnat logging enable\n' \
  > "$FAKE/hexarouting.conf"
logging enabled "10.0.0.2:4739 src 10.0.0.1 (path mtu 512, template every 20s)"
run
if said_across '\[ ok \] UDP export running 10.0.0.2:4739'; then
  ok "a box that is exporting says where to"
else
  bad "an exporting box said nothing: $(printf '%s\n' "$OUTPUT" | grep -i export || echo '<nothing>')"
fi
if said '\[FAIL\].*export'; then
  bad "and it was still counted as a failure"
else
  ok "and it is not a failed check"
fi

echo "--- a dynamic pool with no transport at all ---"
printf '\n' > "$FAKE/hexarouting.conf"
logging disabled "none: nothing is sent over UDP"
printf 'cgnat pool din\n mode dynamic\n' > "$FAKE/bpa_config"
run
if said '\[warn\] a pool runs in dynamic mode and NOTHING is being exported'; then
  ok "translating traffic and recording none of it is said out loud"
else
  bad "a dynamic pool with no export was passed over in silence"
fi
if said '\[FAIL\].*dynamic mode and NOTHING'; then
  bad "and it was failed for it, which an operator may legitimately mean"
else
  ok "and it is a warning, not a failure"
fi
: > "$FAKE/bpa_config"
rm -f "$FAKE/vppctl_ok" "$FAKE/hexarouting.conf"
unset HEXA_CONFIG

# There was a SECOND copy of the verdict block here, `exit 0` and all, left
# behind when the IPFIX section above was inserted. Everything below it - the
# pair checks of #182, the local0 case, and now the daemon list - had never
# run once: the file reported ALL TESTS PASSED after two thirds of itself.
# A test that exits before its assertions is the same defect class as a lab
# that crashes before reaching one, and it is invisible for the same reason.

echo "=== a dataplane interface with no linux_cp pair (#182) ==="
# The defect this file's newest check exists for. FRR only ever sees a pair:
# with none, the port that carries traffic has no netdev, and BGP, OSPF and
# IS-IS are configurable and completely inert. Nothing in the product created
# a pair for the life of the project, and every lab created one by hand, so
# the labs proved a box nobody received.
#
# The listings are ARRANGED HERE and only read by the self-test. A check that
# creates the pair it is checking for cannot report its absence.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\n' > "$FAKE/passwd"
printf '\n' > "$FAKE/groups"
: > "$FAKE/vppctl_ok"
# NEITHER of the two things that make a pair mandatory: no routing protocol
# configured, and no handover record in the tuner's file. Both are arranged as
# absences on purpose - this is the box that must STAY a warning, and it is the
# third state of #334, the one a fix can quietly turn into a new defect.
rm -f "$FAKE/frr_running" "$FAKE/root/etc/hexarouting/tune.env"

# The header is indented, the counter continuation lines are indented, and a
# name is the only thing in column one. Copied from a real `show interface`
# rather than invented, because the parse depends on that shape.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
host-veth-vpp                     1      up          1500/0/0/0     rx packets                  12
                                                                    rx bytes                  1008
local0                            0     down          0/0/0/0
IFACE
: > "$FAKE/show_lcp"
run
if said '\[warn\] no linux_cp pair on: host-veth-vpp'; then
  ok "an unpaired dataplane interface is reported, by name"
else
  bad "an unpaired interface said nothing: $(printf '%s\n' "$OUTPUT" | grep -i lcp || echo '<nothing about it>')"
fi
# Not "something is wrong". Whoever is at the console has to be able to act,
# and the statement that fixes it is one line in the VTY.
if said "linux-name <name>"; then
  ok "and it names the statement that creates the pair"
else
  bad "the report does not say how to create the pair"
fi
# AND THE DEFAULT A CARD GETS (#389): a card named after its Linux card is
# paired under that name by any 'interface' block, so the operator at this
# console is told the shortest way there and not only the override.
if said "paired under that name by any 'interface' block"; then
  ok "and that a card's own block pairs it under the card's name"
else
  bad "the report does not say a card's block pairs it by default"
fi
# A box that forwards CGNAT and runs no routing at all is a supported
# configuration of this product. Failing it over a pair it does not need is
# how a self-test's failures stop being read.
if said '\[FAIL\].*pair'; then
  bad "a box with nothing depending on the pair was FAILED over it: $(printf '%s\n' "$OUTPUT" | grep -E '\[FAIL\].*pair')"
else
  ok "and with neither routing nor a handover record it stays a warning"
fi
# local0 is on every box and is never paired. Reporting it would put a
# permanent warning on every appliance, which is a warning nobody reads.
if said 'pair on:.*local0'; then
  bad "local0 was reported as an interface that wants a pair"
else
  ok "and local0 is not one of them"
fi

echo "=== the same box, with routing configured on it ==="
# The line between worth knowing and broken. Routing is configured, no
# interface is paired, so every protocol the operator configured will sit
# there and never form an adjacency.
printf 'router bgp 65000\n bgp router-id 10.0.0.1\n' > "$FAKE/frr_running"
run
if said "\[FAIL\] routing is configured and NO interface is paired"; then
  ok "it is a failed check, not a warning"
else
  bad "a box whose routing cannot work reported no failure: $(printf '%s\n' "$OUTPUT" | grep -i 'pair' || echo '<nothing about it>')"
fi
if said 'without a pair:.*host-veth-vpp'; then
  ok "and it still names the interface"
else
  bad "the failure does not say which interface has no pair"
fi
rm -f "$FAKE/frr_running"

echo "=== the box the handover was written for: no pair, no router stanza (#334) ==="
# The state the check could not see. A box that hands its ONLY card to the
# dataplane takes a static default route, so `show running-config` has no
# `^router ` line in it and the old check took the softer branch - on the one
# box where a missing pair means nobody can reach it at all.
#
# The handover record is arranged in the tuner's file and read from there by
# the self-test. It is where the VTY wrote it and the only place the answer
# exists: between the commit that names the card and this boot there is
# nothing on the box to read back, which is why this is a file and not a
# question put to the dataplane.
mkdir -p "$FAKE/root/etc/hexarouting"
printf 'HEXA_DATAPLANE_IF=ens18\nHEXA_DATAPLANE_MGMT="ens18 10.95.200.198/24 52:54:00:00:00:01"\n' \
  > "$FAKE/root/etc/hexarouting/tune.env"
run
if said '\[FAIL\] this box handed its only card over and NO pair carries'; then
  ok "a handover box with no pair is a failed check, with no routing anywhere"
else
  bad "the box that cannot be reached was not FAILED: $(printf '%s\n' "$OUTPUT" | grep -iE 'pair' || echo '<nothing about it>')"
fi
if said '\[warn\] no linux_cp pair'; then
  bad "and it was warned about instead: $(printf '%s\n' "$OUTPUT" | grep 'no linux_cp pair')"
else
  ok "and it is not ALSO reported as a warning"
fi
# The record, not a wording: the address is the operator's only way back onto
# a box whose card is in the dataplane, and it comes out of the fixture above
# rather than out of any sentence this product writes.
if said_across '10\.95\.200\.198/24'; then
  ok "and it prints the management path the box recorded, to put back by hand"
else
  bad "the failure does not carry the address the box is reached on: $(printf '%s\n' "$OUTPUT" | grep -iE 'management path|recorded' || echo '<nothing>')"
fi
# The whole failure as it reaches the console, printed once so that what an
# operator is told can be read in this file rather than reconstructed from
# six needles. Not an assertion - the assertions above are.
printf '%s\n' "$OUTPUT" | grep -A9 'handed its only card over' | sed 's/^/    | /'
if said_across 'without a pair:.*host-veth-vpp'; then
  ok "and it names the interface that has no pair"
else
  bad "the handover failure does not say which interface has no pair"
fi
# The FRR half must not be spoken on a box that has no routing at all: a
# failure that lists a reason the box does not have is a failure an operator
# stops reading.
if said 'BGP, OSPF and IS-IS'; then
  bad "a box running no routing protocol was told its adjacencies are down"
else
  ok "and it says nothing about adjacencies on a box running no protocol"
fi

echo "=== a box that did both: handed its card over AND runs a protocol ==="
# Two reasons, one failure. The headline is the handover, because a box
# nobody can log in to is worse than one whose adjacencies are down - but the
# adjacencies are down too, and the operator is owed both facts.
printf 'router bgp 65000\n bgp router-id 10.0.0.1\n' > "$FAKE/frr_running"
run
if said '\[FAIL\] this box handed its only card over and NO pair carries'; then
  ok "the headline is the reason that takes the box off the network"
else
  bad "a box that handed its card over led with something else: $(printf '%s\n' "$OUTPUT" | grep -E '\[FAIL\].*pair' || echo '<no failure about the pair at all>')"
fi
if said 'BGP, OSPF and IS-IS'; then
  ok "and the routing half is still said, under it"
else
  bad "the second reason went unsaid on a box that has both"
fi
rm -f "$FAKE/frr_running" "$FAKE/root/etc/hexarouting/tune.env"

echo "=== the pair exists, which is the state the product produces ==="
# `linux-name vppwan0` under `interface host-veth-vpp`, committed. The TAP is
# itself an interface in VPP's list, and counting it would make every
# correctly paired box look half done - which is why the COUNT is asserted and
# not merely the presence of the ok line: drop tap* from the exclusions and
# this box reports two paired interfaces where it has one.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
host-veth-vpp                     1      up          1500/0/0/0     rx packets                  12
local0                            0     down          0/0/0/0
tap4096                           2      up          1500/0/0/0
IFACE
printf 'itf-pair: [0] host-veth-vpp tap4096 vppwan0 5 type tap\n' > "$FAKE/show_lcp"
run
if said '\[ ok \] 1 dataplane interface\(s\) paired into the kernel'; then
  ok "a paired box says so, and counts one"
else
  bad "a paired box did not report the pair: $(printf '%s\n' "$OUTPUT" | grep -i pair || echo '<nothing>')"
fi
if said 'no linux_cp pair on'; then
  bad "a paired box warned anyway: $(printf '%s\n' "$OUTPUT" | grep 'no linux_cp pair')"
else
  ok "and nothing on it is reported as unpaired"
fi

echo "=== a dataplane holding nothing but local0 ==="
# First boot, and the live medium: the tuner has given VPP no NIC yet. The
# DPDK block above already says why in the operator's terms, so this must not
# repeat it as a warning - and it must not be silent either, because no line
# reads as nothing to report.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
IFACE
: > "$FAKE/show_lcp"
run
if said '\[info\] the dataplane has no interface that could be paired yet'; then
  ok "said as information, once"
else
  bad "a box with no NIC yet said nothing about pairing"
fi
if said '\[warn\] no linux_cp pair|\[FAIL\].*paired'; then
  bad "a box with no NIC yet was warned or failed over a pair it cannot have"
else
  ok "and neither warned nor failed over it"
fi
rm -f "$FAKE/vppctl_ok" "$FAKE/show_interface" "$FAKE/show_lcp"

echo "=== the statistics segment, which the dataplane ABORTS on (#288) ==="
# The one resource with a hard abort behind it: a full statistics segment is
# not a refused interface, it is os_panic and SIGABRT taking the whole
# dataplane. A box on VPP's built-in 32 MB was measured aborting between 7,000
# and 8,000 interfaces, and the tuner sizes the segment above that from the
# interfaces a box is configured for - which leaves the ones it makes at
# runtime, and this reading, as the only thing between the two.
#
# Nothing on an installed box ever looked at it, so the operator's first
# knowledge of the limit was the box being gone. These cases are the three
# distances from that wall plus the two ways the reading itself can be wrong,
# and every one of them is arranged in $FAKE: the machine running this suite
# cannot be made to fill a real statistics segment without creating several
# thousand interfaces on it.
mkcpus "" "0-3" "" 0
touch "$FAKE/vppctl_ok"
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
GigabitEthernet0/8/0              1      up          9000/0/0/0
tap0                              2      up          9000/0/0/0
IFACE
: > "$FAKE/show_lcp"

echo "--- a box with room, on a segment nobody here chose ---"
# The shipped base file explains in a COMMENT where a statseg stanza goes, and
# that comment is copied into startup.conf verbatim. A box carrying only the
# comment has not been sized by anybody, and reading the word rather than the
# stanza would report every shipped box as one somebody had chosen a number for.
printf '# statseg{} DOES belong here, and this is the only place it can go.\n' \
  >> "$FAKE/root/etc/vpp/startup.conf"
cat > "$FAKE/statseg" <<'SEG'
Entry: /sys/vector_rate (type 2)
base 0x7fb3c0000000, size 32.00M, locked, name 'stat segment'
  total: 31.99M, used: 4.20M, free: 27.79M, trimmable: 27.79M
SEG
run
if said_across '\[ ok \] statistics segment: 4.20M of 31.99M used, 3 interface'; then
  ok "it reports what the dataplane holds, and over how many interfaces"
else
  bad "the healthy segment was not reported: $(printf '%s\n' "$OUTPUT" | grep -i statist || echo '<nothing about it>')"
fi
# The point of the issue in one line: the number is VPP's, not this product's.
if said_across "size: VPP's own default, which this product does not set"; then
  ok "and says the size is VPP's own, which is the thing nobody was told"
else
  bad "it did not say where the size came from"
fi
echo "--- the same box, sized by the tuner, on the same numbers ---"
# Proves the provenance line is read off the box and is not a constant: only
# the startup.conf changed.
#
# AND IT IS THE TUNER'S, not somebody's. hexarouting-tune writes a statseg
# stanza for a box whose configuration names more interfaces than VPP's
# default was measured holding (#288), so a stanza in startup.conf is no
# longer proof that an operator chose the number - and telling one from the
# other is the whole of what this line is for.
printf 'statseg {\n  size 1G\n}\n' >> "$FAKE/root/etc/vpp/startup.conf"
run
if said_across "size: chosen by hexarouting-tune from this box's configuration"; then
  ok "a box the tuner sized is not reported as running on the default"
else
  bad "the provenance did not follow the box: $(printf '%s\n' "$OUTPUT" | grep -i 'size:' || echo '<nothing>')"
fi

echo "--- and the same box, sized by its operator ---"
# The stanza in the BASE file, which is where an operator's own goes and the
# one place the tuner steps aside for. Same startup.conf as the case above -
# the tuner concatenates the base into it - so the only thing that tells the
# two boxes apart is which file the number came from.
mkdir -p "$FAKE/root/etc/hexarouting"
printf 'statseg {\n  size 1G\n}\n' > "$FAKE/root/etc/hexarouting/vpp-base.conf"
run
if said_across 'size: set in /etc/hexarouting/vpp-base.conf'; then
  ok "an operator's own stanza is reported as the operator's"
else
  bad "a box whose operator chose the size was told something else: $(printf '%s\n' "$OUTPUT" | grep -i 'size:' || echo '<nothing>')"
fi
rm -f "$FAKE/root/etc/hexarouting/vpp-base.conf"
mkcpus "" "0-3" "" 0

echo "--- half spent, which is the last moment a warning is any use ---"
cat > "$FAKE/statseg" <<'SEG'
Entry: /sys/vector_rate (type 2)
  total: 31.99M, used: 19.20M, free: 12.79M, trimmable: 12.79M
SEG
run
if said_across '\[warn\] statistics segment 60% used: 19.20M of 31.99M'; then
  ok "it is a warning with the fraction spent, not a green line"
else
  bad "a segment 60% spent was not warned about: $(printf '%s\n' "$OUTPUT" | grep -i statist || echo '<nothing about it>')"
fi
if said_across 'ABORTS when this fills, it does not refuse the next interface'; then
  ok "and says the failure is an abort and not a refusal"
else
  bad "the warning did not say what happens when it fills"
fi
if said_across "size 256M .* stanza in /etc/hexarouting/vpp-base.conf"; then
  ok "and names the stanza and the file it goes in"
else
  bad "the warning did not say how to raise it"
fi

echo "--- three quarters spent, which is a box about to lose its dataplane ---"
cat > "$FAKE/statseg" <<'SEG'
Entry: /sys/vector_rate (type 2)
  total: 31.99M, used: 28.80M, free: 3.19M, trimmable: 3.19M
SEG
run
if said_across '\[FAIL\] statistics segment 90% used: 28.80M of 31.99M'; then
  ok "a nearly full segment is a FAILED check"
else
  bad "a segment 90% spent was not a failed check: $(printf '%s\n' "$OUTPUT" | grep -i statist || echo '<nothing about it>')"
fi
if said_across 'os_panic, SIGABRT, the whole dataplane'; then
  ok "and it names the signal the operator will otherwise find on their own"
else
  bad "it did not name what the box does when the segment fills"
fi
# The measured reason the last quarter is not spendable, and the reason 75%
# rather than 95% is the line: the directory is one contiguous block that is
# copied to grow, so a growth holds the old and the new at once.
if said_across 'one block that is COPIED to grow'; then
  ok "and why the remainder is not usable either"
else
  bad "it did not say why the free space is not headroom"
fi

echo "--- a dataplane that does not report it at all ---"
# Unknown is a third state. Reading it as fine is how a box gets a green line
# it has not earned.
rm -f "$FAKE/statseg"
run
if said_across "did not report its statistics segment"; then
  ok "it says the reading is missing"
else
  bad "a box that reported nothing produced no line: $(printf '%s\n' "$OUTPUT" | grep -i statist || echo '<nothing about it>')"
fi
if said_across "unknown - which is not the same as being far from it"; then
  ok "and does not read the silence as room"
else
  bad "it did not distinguish unknown from safe"
fi
if said '\[ ok \] statistics segment'; then
  bad "it reported a segment it never read"
else
  ok "and no green line was printed for a segment nobody read"
fi
rm -f "$FAKE/vppctl_ok" "$FAKE/show_interface" "$FAKE/show_lcp" "$FAKE/statseg"

echo "=== the routing daemons, which is the list the VTY's offers rest on ==="
# #187: the VTY accepts `router ospf6`, and ospf6d was in no list the product
# shipped - not the installer's, not this check's. A box that cannot run a
# protocol it can be configured for has to say so at the console, before
# somebody configures it.
: > "$FAKE/frr_running"
printf 'bgpd ospfd isisd zebra staticd mgmtd\n' > "$FAKE/frr_daemons"
run
if said '\[warn\] routing daemons not running:.*ospf6d'; then
  ok "a box without ospf6d is told it is missing ospf6d"
else
  bad "ospf6d missing went unreported: $(printf '%s\n' "$OUTPUT" | grep -i 'routing daemons' || echo '<nothing>')"
fi
# The other half. Without it the check above is also satisfied by a self-test
# that warns whatever the box is running, which is an alarm nobody reads.
# Asserted on the WARNING being gone rather than on ospf6d appearing in the
# "up" line: that line prints vtysh's own answer, so the name is in it whether
# or not this check looked for it.
printf 'bgpd ospfd ospf6d isisd zebra staticd mgmtd\n' > "$FAKE/frr_daemons"
run
if said '\[warn\] routing daemons not running'; then
  bad "a complete box was still warned: $(printf '%s\n' "$OUTPUT" | grep -i 'routing daemons' || echo '<nothing>')"
elif said '\[ ok \] routing daemons up:'; then
  ok "and a box that has every one of them is reported up, not warned"
else
  bad "a complete box said nothing about its routing daemons"
fi
rm -f "$FAKE/frr_daemons" "$FAKE/frr_running"

echo "=== the cores the dataplane got, against the cores it was given (#172) ==="
# The self-test already printed "full tuning active: corelist-workers ...".
# That is the FILE. Until now nothing on an installed box read what the
# dataplane actually got, and the two come apart without a symptom: a VPP that
# has been running since before the profile was rewritten keeps the cores it
# started with, and a kernel command line that did not get updated leaves a
# poll-mode worker sharing its core with the scheduler. In both the box
# forwards, answers and delivers a fraction of its capacity.
#
# So each case below makes those three sources disagree in exactly one way.
# The self-test never writes any of them.

# The shape the tuner produces: four cpus, one for the OS and VPP's main
# thread, three isolated and carrying one worker each.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
run
if said '\[ ok \] dataplane workers hold the cores the profile names: 1 2 3'; then
  ok "a dataplane on the cores its profile names is reported so, by core"
else
  bad "a correctly pinned dataplane was not reported: $(printf '%s\n' "$OUTPUT" | grep -i 'workers hold' || echo '<nothing>')"
fi
if said '\[ ok \] and the kernel is isolating every one of them'; then
  ok "and the kernel isolating those cores is a separate line"
else
  bad "nothing said whether the kernel is isolating the worker cores"
fi
# Named one by one rather than as "no [FAIL] anywhere": the rest of this fake
# box is missing on purpose - no VERSION stamp, no oom_score_adj, no FRR - and
# a blanket assertion would be satisfied by fixing THOSE, which has nothing to
# do with cores.
if said '\[FAIL\] worker core|\[FAIL\] startup.conf pins workers'; then
  bad "a correct box failed a core check: $(printf '%s\n' "$OUTPUT" | grep -E '\[FAIL\] (worker core|startup.conf pins)' | head -1)"
else
  ok "and neither core check is failed on a correct box"
fi

# One worker core the kernel is not isolating. This is the silent one: the
# worker runs, the counters are clean, and the core is shared with everything
# the scheduler has to place.
mkcpus "1-2" "0-3" "1-3" 0 1 2 3
run
if said '\[FAIL\] worker core\(s\) 3 are NOT isolated by this kernel'; then
  ok "a worker on a core the kernel is not isolating is a FAILED check"
else
  bad "a worker outside isolcpus went unreported: $(printf '%s\n' "$OUTPUT" | grep -i isolat | head -2 || echo '<nothing>')"
fi
# Which core, and which cores the kernel IS isolating. "Something is wrong"
# leaves whoever is at the console with a box and no next step.
if said 'isolcpus gives 1 2'; then
  ok "and it names what the kernel is isolating instead"
else
  bad "the failure does not say what the kernel is isolating"
fi
if said 'update-grub'; then
  ok "and what to do about it"
else
  bad "the failure does not say how to fix it"
fi
# The other assertion must not fire here: VPP does hold the cores the file
# names. A check that goes red for every kind of wrong is one that locates
# nothing.
if said '\[FAIL\] startup.conf pins workers'; then
  bad "the profile-mismatch check also fired, on a box whose profile matches"
else
  ok "and the profile-mismatch check stays quiet, because the profile matches"
fi

# A VPP older than the profile it is judged against: the file was rewritten
# for three workers, the running dataplane still holds the two it started
# with. "full tuning active" above reports the file, so it says yes.
mkcpus "1-3" "0-3" "1-3" 0 1 2
run
if said_across '\[FAIL\] startup.conf pins workers to cores the dataplane is not holding pinned: 1 2 3'; then
  ok "a dataplane older than its own profile is a FAILED check"
else
  bad "a stale VPP was reported as tuned: $(printf '%s\n' "$OUTPUT" | grep -iE 'workers hold|pins workers' || echo '<nothing>')"
fi
if said 'systemctl restart vpp.service'; then
  ok "and it says what to do about it"
else
  bad "the failure does not say how to fix it"
fi

# The profile names workers and the dataplane has none: VPP is forwarding on
# its main thread. This is the whole of the box's capacity, missing.
mkcpus "1-3" "0-3" "1-3" 0
run
if said_across '\[FAIL\] startup.conf pins workers to cores the dataplane has none of pinned: 1 2 3'; then
  ok "a dataplane with no worker thread at all is a FAILED check"
else
  bad "a dataplane forwarding on its main thread was not reported: $(printf '%s\n' "$OUTPUT" | grep -i worker | head -2 || echo '<nothing>')"
fi

# What the control plane is left with. Reported, not capped - the same
# position this product takes on MemoryMax for frr.service, and for the same
# reason: how many cores to hand back is a throughput trade that belongs to
# the operator. What the box owes them is the number.
mkcpus "1-31" "0-31" "1-31" 0 1 2 3
run
if said '\[warn\] the control plane has ONE cpu \(0\), with VPP.s main thread'; then
  ok "one cpu for everything that is not a worker is a warning, naming the cpu"
else
  bad "a box with one control-plane cpu said nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'control plane' || echo '<nothing>')"
fi
if said 'HEXA_MAX_WORKERS'; then
  ok "and it names the setting that hands cores back"
else
  bad "the warning does not name the remedy the product already ships"
fi
if said '\[FAIL\].*control plane'; then
  bad "and it was counted as a failed check, which a tuning trade is not"
else
  ok "and it is not a failed check: the trade is the operator's to make"
fi

# The same one cpu, on a box that has nothing to hand back. A two-core box
# cannot do better, and warning it would be an alarm that is always on - which
# is the same as no alarm.
mkcpus "1" "0-1" "1" 0 1
run
if said '\[warn\].*ONE cpu'; then
  bad "a two-core box was warned about a trade it cannot make"
else
  ok "a box with one isolated core is not warned: it has nothing to give back"
fi
if said '\[ ok \] control plane has 1 cpu\(s\): 0, with VPP.s main thread'; then
  ok "and the count is still reported, with the main thread named beside it"
else
  bad "the control-plane share was not reported at all: $(printf '%s\n' "$OUTPUT" | grep -i 'control plane' || echo '<nothing>')"
fi

# A box the tuner judged too small for a dedicated worker. There are no cores
# to compare, and the first-boot and small-box cases are already reported
# above: this must add nothing.
mkcpus "" "0-1" "" 0
run
if said 'worker core|workers hold|control plane has'; then
  bad "a box with no workers was told about cores it does not have: $(printf '%s\n' "$OUTPUT" | grep -iE 'worker|control plane' | head -2)"
else
  ok "a box with no workers is not told anything about worker cores"
fi

# VPP running outside its unit. No drop-in on vpp.service reaches it and
# neither does MainPID, so where its workers are cannot be read - and silence
# there would read as nothing to report.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
echo 0 > "$FAKE/mainpid"
run
if said '\[warn\] vpp.service owns no process, so where the dataplane.s workers'; then
  ok "a VPP outside its unit is reported as unreadable, not as correct"
else
  bad "a hand-started VPP's cores were passed over: $(printf '%s\n' "$OUTPUT" | grep -iE 'workers hold|owns no process' || echo '<nothing>')"
fi
if said '\[ ok \] dataplane workers hold'; then
  bad "and it claimed the workers are where they should be without reading them"
else
  ok "and nothing was claimed about cores that were never read"
fi
rm -f "$FAKE/mainpid"
rm -rf "$FAKE/root"

echo "=== the rx-queues line says a number, or says what the default costs ==="
# The fix this came with had nothing holding it, which is how it got there in
# the first place: the || sat after "head -1", and head succeeds on empty
# input, so the fallback could never run and a box with no num-rx-queues
# printed "[ ok ] rx queues:" and stopped. A label with no value is an
# assertion that cannot fail, in the half of the self-test that reports.
#
# mkcpus writes a startup.conf with no num-rx-queues, which is the box in
# question: the tuner leaves the line out when it has nothing to say, and that
# is the common case rather than an odd one.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
run
if said '\[ ok \] rx queues requested: *$'; then
  bad "the rx-queues line printed a label and no value at all"
elif said '\[ ok \] rx queues requested: default'; then
  ok "a box whose profile names no rx queues says so, and says what it means"
else
  bad "nothing reported rx queues: $(printf '%s\n' "$OUTPUT" | grep -i 'rx queue' || echo '<nothing>')"
fi
# AND IT SAYS THAT IT IS A REQUEST. The number comes from
# /etc/vpp/startup.conf, so it is what the tuner asked the dataplane for and
# not what the dataplane got - a card with fewer queues gets what it has, and
# #330 is the box where seven were asked for on a NIC with one. The cores two
# sections down are read back off the running process and say so; this one
# cannot be, so the word is what keeps the line honest.
if said '\[ ok \] rx queues requested:'; then
  ok "and the line says the number is a request, not a reading"
else
  bad "the rx-queues line no longer says it is a request, so a number the \
dataplane may have refused reads as one it took: $(printf '%s\n' "$OUTPUT" \
| grep -i 'rx queue' || echo '<nothing>')"
fi
rm -rf "$FAKE/root"

echo "=== the CGNAT plugin, which is the whole of what this box is for (#222) ==="
# A dataplane that came up without the plugin answers `show version` perfectly
# well, forwards nothing through the CGNAT and fails every pool command. The
# check for it is one grep, and nothing held it: a grep whose needle stops
# matching passes for the same reason a correct box does.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
: > "$FAKE/vppctl_ok"
printf 'Plugin path is: /usr/lib/vpp_plugins\n bpa_plugin.so\n dpdk_plugin.so\n' \
  > "$FAKE/show_plugins"
run
if said '\[ ok \] CGNAT plugin loaded'; then
  ok "a dataplane carrying the plugin is reported so"
else
  bad "a dataplane with bpa_plugin.so loaded was not reported: $(printf '%s\n' "$OUTPUT" | grep -i plugin || echo '<nothing>')"
fi
printf 'Plugin path is: /usr/lib/vpp_plugins\n dpdk_plugin.so\n' > "$FAKE/show_plugins"
run
if said '\[FAIL\] CGNAT plugin missing'; then
  ok "and a dataplane without it is a FAILED check, not a warning"
else
  bad "a box that can do no CGNAT at all passed: $(printf '%s\n' "$OUTPUT" | grep -i plugin || echo '<nothing>')"
fi
rm -f "$FAKE/show_plugins"

echo "=== a vpp.service in a restart loop (#222) ==="
# "A service in a loop can be active at the instant you look, and only
# NRestarts tells the story" is what the CHANGELOG says about this check. The
# stub answered 0 for every run, so the two branches that carry that sentence
# had never been executed by anything.
printf 'Plugin path\n bpa_plugin.so\n' > "$FAKE/show_plugins"
echo 0 > "$FAKE/nrestarts"
run
if said '\[ ok \] vpp.service stable \(0 restarts\)'; then
  ok "a dataplane that has not restarted is reported as stable"
else
  bad "a stable vpp.service was not reported: $(printf '%s\n' "$OUTPUT" | grep -i restart || echo '<nothing>')"
fi
echo 2 > "$FAKE/nrestarts"
run
if said '\[warn\] vpp.service restarted 2 time\(s\)'; then
  ok "two restarts is a warning, and says how many"
else
  bad "two restarts said nothing: $(printf '%s\n' "$OUTPUT" | grep -i restart || echo '<nothing>')"
fi
if said '\[FAIL\].*restart'; then
  bad "two restarts was counted as a failure; a box that restarted twice and settled is not looping"
else
  ok "and two restarts is not a failed check"
fi
echo 7 > "$FAKE/nrestarts"
# The journal the failure prints under itself. Whoever is standing at that
# console cannot reach the box any other way yet, so the reason has to come
# with the finding - and it has to be THIS box's reason: until this stub
# existed the lines came from the systemd of whatever machine ran the suite.
printf 'vpp[811]: dpdk: EAL init failed, no hugepages\n' > "$FAKE/journal_vpp"
run
if said '\[FAIL\] vpp.service has restarted 7 times: it is looping'; then
  ok "a loop is a FAILED check, and names the count"
else
  bad "a looping vpp.service passed: $(printf '%s\n' "$OUTPUT" | grep -i restart || echo '<nothing>')"
fi
if said 'EAL init failed, no hugepages'; then
  ok "and the journal under it is this box's, not the one running the suite"
else
  bad "the looping box's own journal did not reach the console, so the \
operator is told the dataplane is looping and not why: $(printf '%s\n' "$OUTPUT" | grep -A3 -i 'journal' | tr '\n' ' ')"
fi
rm -f "$FAKE/nrestarts" "$FAKE/journal_vpp"

echo "=== IS-IS frames punted to the kernel (#222, #168) ==="
# The reason this line exists: a box with an IS-IS instance and no OSI punt
# commits cleanly, brings isisd up, reads back correctly and forms no adjacency
# ever, with the only evidence in a dataplane counter nothing surfaces. It is
# silent in exactly the way the boot self-test exists for, and nothing held it.
printf 'router isis CORE\n net 49.0001.0000.0000.0001.00\n' > "$FAKE/frr_running"
printf 'itf-pair: [0] host-veth-vpp tap0 host-veth-vpp 2 type tap osi 0x83\n' \
  > "$FAKE/show_lcp"
run
if said '\[ ok \] IS-IS frames are punted to the kernel'; then
  ok "a box running IS-IS with the OSI punt in place is reported so"
else
  bad "the punt was in place and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'IS-IS\|osi' || echo '<nothing>')"
fi
printf 'itf-pair: [0] host-veth-vpp tap0 host-veth-vpp 2 type tap\n' > "$FAKE/show_lcp"
run
if said '\[warn\] IS-IS is configured and its frames are NOT punted'; then
  ok "and without the punt it is reported, on a box that otherwise looks healthy"
else
  bad "IS-IS configured and not punted said nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'IS-IS\|osi' || echo '<nothing>')"
fi
if said 'adjacency will never form'; then
  ok "and says what the operator will see instead, which is nothing"
else
  bad "the warning does not say what goes wrong"
fi
# And it must stay quiet on a box that is not running IS-IS at all. A line
# about a protocol nobody configured is the alarm #205 and #208 are about.
printf 'router bgp 65000\n' > "$FAKE/frr_running"
run
if said 'IS-IS'; then
  bad "a box with no IS-IS was told about IS-IS: $(printf '%s\n' "$OUTPUT" | grep -i 'IS-IS' | head -1)"
else
  ok "a box not running IS-IS is not told about the OSI punt"
fi
rm -f "$FAKE/show_lcp"

echo "=== RPKI, whose absence presents as something else entirely (#222) ==="
# bgpd refuses to start when told to load a module it cannot find, so a missing
# RPKI module presents as "bgpd is not running" and sends the reader after the
# wrong thing. Three states, and the middle one is the one that matters: a
# cache configured and not connected marks every route notfound, quietly.
printf 'router bgp 65000\n' > "$FAKE/frr_running"
rm -f "$FAKE/rpki"
run
if said '\[warn\] RPKI module not loaded'; then
  ok "a module that is not loaded is reported, with what to install"
else
  bad "RPKI absent said nothing: $(printf '%s\n' "$OUTPUT" | grep -i rpki || echo '<nothing>')"
fi
printf 'RPKI Cache Servers\n' > "$FAKE/rpki"
run
if said '\[ ok \] RPKI module loaded \(no cache configured'; then
  ok "loaded with no cache is reported as loaded, and says nothing is validated"
else
  bad "RPKI loaded with no cache: $(printf '%s\n' "$OUTPUT" | grep -i rpki || echo '<nothing>')"
fi
printf 'router bgp 65000\n rpki cache 192.0.2.1 8282 preference 1\n' \
  > "$FAKE/frr_running"
printf 'host: 192.0.2.1 port: 8282 (connected)\n' > "$FAKE/rpki"
run
if said '\[ ok \] RPKI: 1 cache\(s\) configured and connected'; then
  ok "a connected cache is reported, and counted"
else
  bad "a connected cache: $(printf '%s\n' "$OUTPUT" | grep -i rpki || echo '<nothing>')"
fi
printf 'host: 192.0.2.1 port: 8282\n' > "$FAKE/rpki"
run
if said '\[warn\] RPKI: 1 cache\(s\) configured, NONE connected'; then
  ok "a configured cache that is not connected is reported"
else
  bad "a disconnected cache passed silently: $(printf '%s\n' "$OUTPUT" | grep -i rpki || echo '<nothing>')"
fi
if said 'every route is notfound'; then
  ok "and says what that does to a policy keyed on notfound"
else
  bad "the warning does not say what a disconnected cache costs"
fi
rm -f "$FAKE/rpki" "$FAKE/frr_running" "$FAKE/show_plugins"
rm -rf "$FAKE/root"

echo "=== the two latency settings, which cost throughput and no counter (#222) ==="
# Neither of these stops the box working, and neither shows up as a drop
# counter. A deep C-state costs the first packets of a burst while the core
# wakes up, and a governor that is not `performance` runs the poll loop at a
# clock the kernel chose for an idle machine - on a thread that never sleeps
# and therefore never looks idle to the governor's own heuristics.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
mkdir -p "$FAKE/root/proc" "$FAKE/root/sys/devices/system/cpu/cpufreq/policy0"
echo "BOOT_IMAGE=/vmlinuz isolcpus=1-3 processor.max_cstate=1 intel_idle.max_cstate=0" \
  > "$FAKE/root/proc/cmdline"
echo performance > "$FAKE/root/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"
run
if said '\[ ok \] deep C-states disabled on the kernel command line'; then
  ok "a tuned command line is reported"
else
  bad "processor.max_cstate=1 was on the command line and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'c-state' || echo '<nothing>')"
fi
if said '\[ ok \] cpufreq governor: performance'; then
  ok "and so is the governor it was set to"
else
  bad "the governor was performance and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i governor || echo '<nothing>')"
fi
echo "BOOT_IMAGE=/vmlinuz isolcpus=1-3" > "$FAKE/root/proc/cmdline"
echo powersave > "$FAKE/root/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"
run
if said '\[warn\] deep C-states still enabled'; then
  ok "a command line without it is reported, and says what it costs"
else
  bad "deep C-states were enabled and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'c-state' || echo '<nothing>')"
fi
if said "\\[warn\\] cpufreq governor is 'powersave', not performance"; then
  ok "and a governor that is not performance is named, with what it is"
else
  bad "a powersave governor passed silently: $(printf '%s\n' "$OUTPUT" | grep -i governor || echo '<nothing>')"
fi
# A machine with no cpufreq at all is a virtual one, and telling it to set a
# governor it does not have is an instruction nobody can follow.
rm -rf "$FAKE/root/sys/devices/system/cpu/cpufreq"
run
if said '\[info\] no cpufreq exposed'; then
  ok "a box with no cpufreq is told there is nothing to set, not warned"
else
  bad "a box with no cpufreq: $(printf '%s\n' "$OUTPUT" | grep -i 'cpufreq\|governor' || echo '<nothing>')"
fi

echo "=== the C-state line against tune.env, not against the command line alone (#163) ==="
# /proc/cmdline is what this boot GOT. tune.env is what was ASKED FOR. The
# line above read only the first, so a box with HEXA_LOW_LATENCY=0 - deep
# C-states left on, on purpose - was warned that deep C-states were still
# enabled and advised to "set HEXA_LOW_LATENCY=0". The console was telling the
# operator to do the thing they had already done, about a state they chose.
mkdir -p "$FAKE/root/etc/hexarouting"
echo "HEXA_LOW_LATENCY=0" > "$FAKE/root/etc/hexarouting/tune.env"
echo "BOOT_IMAGE=/vmlinuz isolcpus=1-3" > "$FAKE/root/proc/cmdline"
run
if said '\[info\] deep C-states left enabled on purpose'; then
  ok "HEXA_LOW_LATENCY=0 with no C-state parameter is reported as a choice"
else
  bad "a box that turned the latency knobs off was not told it had: $(printf '%s\n' "$OUTPUT" | grep -i 'c-state' || echo '<nothing>')"
fi
if said '\[warn\] deep C-states still enabled'; then
  bad "a box that set HEXA_LOW_LATENCY=0 is still warned about the state it chose"
else
  ok "and not warned about it, nor told to set the value it already set"
fi

# The case that had no line at all: the file says 0 and this boot's command
# line still carries the parameters. That is a box running a command line
# older than the file that generates it - the tuning in effect is not the
# tuning requested, which is the whole criterion.
echo "BOOT_IMAGE=/vmlinuz isolcpus=1-3 processor.max_cstate=1" \
  > "$FAKE/root/proc/cmdline"
run
if said "booted a command line older"; then
  ok "tune.env and the command line disagreeing is reported, with the reason"
else
  bad "the file says 0, the command line disables C-states, and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'c-state' || echo '<nothing>')"
fi
if said '\[ ok \] deep C-states disabled on the kernel command line'; then
  bad "the box reported the tuning as correct while tune.env asks for the opposite"
else
  ok "and it is not reported as a tuned box: the two readings do not agree"
fi

# A value tune.env must not carry. apply-tuning refuses it and uses 1; the
# self-test MUST say the same thing, because two components disagreeing about
# one file is worse than either of them being wrong.
echo "HEXA_LOW_LATENCY=true" > "$FAKE/root/etc/hexarouting/tune.env"
echo "BOOT_IMAGE=/vmlinuz isolcpus=1-3 processor.max_cstate=1" \
  > "$FAKE/root/proc/cmdline"
run
if said "HEXA_LOW_LATENCY='true'"; then
  ok "a value the box does not accept is refused on the console, quoted"
else
  bad "HEXA_LOW_LATENCY=true passed silently: $(printf '%s\n' "$OUTPUT" | grep -i low_latency || echo '<nothing>')"
fi
if said "Only 1 .deep C-states off," && said "and 0 .leave all three alone"; then
  ok "and the refusal says what the box does accept, both values"
else
  bad "the refusal does not say what IS accepted: $(printf '%s\n' "$OUTPUT" | grep -i low_latency || echo '<nothing>')"
fi
if said '\[ ok \] deep C-states disabled on the kernel command line'; then
  ok "and the refused value is read as 1, which is what apply-tuning does with it"
else
  bad "the refused value was not read as the shipped default: $(printf '%s\n' "$OUTPUT" | grep -i 'c-state' || echo '<nothing>')"
fi
rm -f "$FAKE/root/etc/hexarouting/tune.env"

# The readback. A parameter the kernel IGNORED leaves /proc/cmdline saying it
# was asked for and the kernel behaving as though it was not, so the green
# line above can be about nothing at all.
run
if said "the kernel exposes no idle-state driver"; then
  ok "a box with no idle-state driver is told the parameters act on nothing"
else
  bad "no cpuidle driver and the box still reported only the request: $(printf '%s\n' "$OUTPUT" | grep -i 'idle' || echo '<nothing>')"
fi
mkdir -p "$FAKE/root/sys/devices/system/cpu/cpuidle"
echo intel_idle > "$FAKE/root/sys/devices/system/cpu/cpuidle/current_driver"
run
if said "the kernel exposes no idle-state driver"; then
  bad "a box WITH an idle-state driver was told it has none"
else
  ok "and a box that has one is not told that it does not"
fi
rm -rf "$FAKE/root/sys/devices/system/cpu/cpuidle"

echo "=== requested, in effect, and the two states that are neither (#163) ==="
# What is arranged here is the RENDERING of the four answers the tuner's
# --report-effective can give. The reader that produces them is measured next
# door in appliance/test_tune.py, against synthetic boxes whose kernel took
# the tuning and boxes whose kernel refused it: that is where the reading
# lives, and this is where the printing does.
#
# THE REAL TUNER, against a fake root, for every box but two. A canned report
# here would be the shape of that report agreed between two files with nothing
# comparing them, which is the shape of the defect this check exists for. Only
# "there is no tuner" and "the tuner answered nothing" cannot be arranged by a
# tuner that works, and those two get HEXA_TUNE.
tuned_root() {  # <isolated> <nohz_full, or - for a kernel that exposes none>
  R=$FAKE/root                    # <extra parameter, or empty>
  rm -rf "$R"
  mkdir -p "$R/proc" "$R/etc/default/grub.d" "$R/sys/devices/system/cpu"
  # TWO parameters and not one, on every box here: with a single parameter
  # the box that refuses it has nothing left in effect, and the summary line
  # is then absent because there was nothing to count rather than because
  # something was refused. Measured - the assertion below that the green
  # summary must not appear beside a refusal went green on a self-test that
  # printed it unconditionally, because the box could not reach it either way.
  _cmd="isolcpus=1-3 nohz_full=1-3${3:+ $3}"
  printf '# Generated by hexarouting-tune. Do not edit by hand.\n' \
    > "$R/etc/default/grub.d/hexarouting.cfg"
  printf 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT %s"\n' \
    "$_cmd" >> "$R/etc/default/grub.d/hexarouting.cfg"
  printf 'BOOT_IMAGE=/vmlinuz ro quiet %s\n' "$_cmd" > "$R/proc/cmdline"
  printf '%s\n' "$1" > "$R/sys/devices/system/cpu/isolated"
  [ "$2" = "-" ] || printf '%s\n' "$2" > "$R/sys/devices/system/cpu/nohz_full"
}

# The kernel took what it was asked for.
tuned_root "1-3" "1-3" ""
run
if said '\[ ok \] every tuning rule this box can read back is in effect'; then
  ok "a box whose kernel took the tuning says so, and counts what it read"
else
  bad "a box running the tuning it asked for did not say so: $(printf '%s\n' "$OUTPUT" | grep -i 'tuning rule\|in effect' || echo '<nothing>')"
fi

# THE DEFECT ITSELF: the parameter is on this boot's command line and the
# kernel is not doing it. /proc/cmdline says isolcpus=1-3 for the life of the
# box, and nothing is isolated - while nohz_full beside it did take, so this
# box has tuning in effect and tuning that is not.
tuned_root "" "1-3" ""
run
if said '\[warn\] requested and NOT in effect: isolcpus'; then
  ok "a parameter the kernel refused is named as refused, not as set"
else
  bad "the kernel refused isolcpus and the box reported it as tuned: $(printf '%s\n' "$OUTPUT" | grep -i 'isolcpus\|in effect' || echo '<nothing>')"
fi
if said 'were asked for and the kernel is not isolating them'; then
  ok "and says what the kernel is doing instead, not only that it differs"
else
  bad "the refusal does not say what the kernel is doing: $(printf '%s\n' "$OUTPUT" | grep -i 'isolcpus' || echo '<nothing>')"
fi
# THE THIRD STATE MUST NOT BE ABSORBED BY THE SUMMARY. One parameter took and
# one did not, so a summary keyed on what took alone still has something to
# report - and reporting it here is the appliance saying it is tuned on a box
# where the kernel refused half of what it was given.
if said '\[ ok \] every tuning rule this box can read back is in effect'; then
  bad "a box with a refused parameter still printed the green summary: $(printf '%s\n' "$OUTPUT" | grep -i 'tuning rule' || echo '<nothing>')"
else
  ok "and the green summary is not printed beside a parameter that was refused"
fi

# THE FOURTH STATE. nohz_full is on the command line and this kernel exposes
# no /sys/devices/system/cpu/nohz_full at all, so whether it took cannot be
# read here. Guessing either way prints a measurement nobody took.
tuned_root "1-3" "-" ""
run
if said '\[info\] cannot be read on this box: nohz_full'; then
  ok "a parameter with no readback here says so, rather than guessing"
else
  bad "a parameter whose effect cannot be read was reported anyway: $(printf '%s\n' "$OUTPUT" | grep -i 'nohz_full' || echo '<nothing>')"
fi
if said '\[warn\] requested and NOT in effect: nohz_full'; then
  bad "a parameter nothing here can read was reported as refused by the kernel"
fi

# A parameter this build has no reader for. It cannot reach a shipped box -
# appliance/test_tune.py compares READBACK against the line the tuner really
# generates - so this is the build where somebody adds a knob and not a way to
# read it, and the console has to say which of the two it is.
tuned_root "1-3" "1-3" "nmi_watchdog=0"
run
if said '\[warn\] no way to read back a parameter this box sets: nmi_watchdog'; then
  ok "a knob shipped without a reader is named, not passed over in silence"
else
  bad "a parameter with no readback in the product was reported as fine: $(printf '%s\n' "$OUTPUT" | grep -i 'nmi_watchdog\|read back' || echo '<nothing>')"
fi

# A tuner that answers nothing at all. `true` is on every box, prints nothing
# and exits 0, which is the one case a working tuner cannot be made to show:
# silence must not read as a box with no tuning to report.
tuned_root "1-3" "1-3" ""
HEXA_TUNE=true
run
HEXA_TUNE=
if said '\[warn\] hexarouting-tune could not say what tuning is in effect'; then
  ok "a tuner that answered nothing leaves the box unmeasured, not confirmed"
else
  bad "a tuner that printed nothing passed as a box with nothing wrong: $(printf '%s\n' "$OUTPUT" | grep -i 'tuning' || echo '<nothing>')"
fi

# And the box that has no tuner on it. Different line from a box whose tuning
# did not take: one is a machine to look at, the other is an install to repair.
HEXA_TUNE=hexarouting-tune-is-not-installed
run
HEXA_TUNE=
if said '\[warn\] hexarouting-tune is not on this box'; then
  ok "a box with no tuner says nothing could look, not that nothing is wrong"
else
  bad "a box with no hexarouting-tune reported on its tuning anyway: $(printf '%s\n' "$OUTPUT" | grep -i 'tuning\|hexarouting-tune' || echo '<nothing>')"
fi

# And the box that asked for nothing, which is every other box in this file:
# a fake root with no generated GRUB file has requested no boot parameter, and
# that is not the same as having asked and been refused.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/proc"
printf 'BOOT_IMAGE=/vmlinuz ro quiet\n' > "$FAKE/root/proc/cmdline"
run
if said '\[info\] not requested on this box: the kernel command line'; then
  ok "a box that asked for no tuning is not reported as one that lost it"
else
  bad "a box that requested nothing was not distinguished from one refused: $(printf '%s\n' "$OUTPUT" | grep -i 'command line' || echo '<nothing>')"
fi
rm -rf "$FAKE/root"

echo "=== the records volume, which is a legal requirement here (#222) ==="
# In dynamic mode the allocation log IS the record the Marco Civil requires to
# be kept for a year. It has its own filesystem so a full log cannot stop the
# box working, and fstab mounts it nofail - which means a volume that never
# mounted leaves the box writing records onto the root filesystem, working
# perfectly, until the root filesystem fills.
mkdir -p "$FAKE/root/var/log/hexarouting"
echo /dev/vda2 > "$FAKE/mnt_root"
echo /dev/vdb1 > "$FAKE/mnt_records"
run
if said '\[ ok \] records on their own filesystem: /dev/vdb1'; then
  ok "a records volume of its own is reported, and named"
else
  bad "the records volume was separate and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'records on' || echo '<nothing>')"
fi
if said 'free of'; then
  ok "and says how much room is left on it"
else
  bad "the line does not say how full the records volume is"
fi
# The volume that did not mount: same directory, same box, one source.
echo /dev/vda2 > "$FAKE/mnt_records"
run
if said '\[warn\] connection records are on the ROOT filesystem'; then
  ok "records on the root filesystem is reported"
else
  bad "records were on the root filesystem and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'records' || echo '<nothing>')"
fi
if said 'stop this box writing its own'; then
  ok "and says what a full log does to the rest of the box"
else
  bad "the warning does not say what it costs"
fi
# On the live medium nothing is persisted anyway, and warning about it there
# would be an alarm that is always on for a reason the reader cannot act on.
mkdir -p "$FAKE/root/run/live/medium/live"
: > "$FAKE/root/run/live/medium/live/filesystem.squashfs"
run
if said '\[info\] live medium: records are not persisted anyway'; then
  ok "the live medium says so instead of warning about a disk it does not have"
else
  bad "the live medium was warned about the root filesystem: $(printf '%s\n' "$OUTPUT" | grep -i 'records\|live medium' || echo '<nothing>')"
fi

echo "=== the records mirror, the one copy a reinstall cannot rebuild (#83) ==="
# With one disk, a single failure on a box under a one-year retention
# obligation loses the RECORDS. That is a different class of loss from
# downtime: downtime ends with a reinstall and this does not end at all.
#
# So the box can be installed with the records on two disks - and a mirror
# nobody is told about is a mirror that silently becomes one disk, and then
# zero. Every state below is one /proc/mdstat can be in on a box that is
# forwarding perfectly, which is why none of them can be arranged on the
# machine running these tests: the disk has to have died.
rm -f "$FAKE/mnt_root" "$FAKE/mnt_records"
rm -rf "$FAKE/root"
mkdir -p "$FAKE/root/var/log/hexarouting" "$FAKE/root/proc"
echo /dev/vda2 > "$FAKE/mnt_root"
mdstat() { mkdir -p "$FAKE/root/proc"; cat > "$FAKE/root/proc/mdstat"; }

echo /dev/md0 > "$FAKE/mnt_records"
mdstat <<'MD'
Personalities : [raid1]
md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
run
if said '\[ ok \] records mirrored on 2 disks \(vdb1 vda3\)'; then
  ok "a healthy mirror is reported, and names the disks it is on"
else
  bad "a two-disk mirror said nothing: $(printf '%s\n' "$OUTPUT" | grep -i mirror || echo '<nothing>')"
fi

# The state this whole check exists for. Everything about this box is fine:
# the volume is mounted, the records are being written, the box forwards.
mdstat <<'MD'
Personalities : [raid1]
md0 : active raid1 vda3[0]
      2094080 blocks super 1.2 [2/1] [U_]

unused devices: <none>
MD
run
if said '\[FAIL\] the records mirror is DEGRADED: 1 of 2 disks'; then
  ok "a disk that has died under the mirror is a FAILED check, with the count"
else
  bad "a degraded mirror was not reported: $(printf '%s\n' "$OUTPUT" | grep -i mirror || echo '<nothing>')"
fi
# Why it is worth a site visit, on the console of a box that is working.
if said 'a reinstall does not put'; then
  ok "and says that this loss is not the kind a reinstall undoes"
else
  bad "the degraded report does not say what is at stake"
fi
if said "mdadm --add"; then
  ok "and says what to do about it"
else
  bad "the degraded report does not say how to repair it"
fi

# A rebuild is the GOOD case in progress - a disk was replaced and is being
# copied onto. Reported as a failure it would train whoever reads this console
# to ignore the line that matters.
mdstat <<'MD'
Personalities : [raid1]
md0 : active raid1 vdc1[2] vda3[0]
      2094080 blocks super 1.2 [2/1] [U_]
      [==>..................]  recovery = 12.5% (262144/2094080) finish=0.1min speed=52428K/sec

unused devices: <none>
MD
run
if said '\[warn\] the records mirror is REBUILDING: 1 of 2 disks, 12.5%'; then
  ok "a replaced disk being copied onto is a warning with its progress"
else
  bad "a rebuilding mirror: $(printf '%s\n' "$OUTPUT" | grep -i mirror || echo '<nothing>')"
fi
if said '\[FAIL\].*mirror'; then
  bad "a mirror that is repairing itself was reported as a failure"
else
  ok "and not a failure: nothing is lost and nobody has to be called"
fi

# Both legs present and still copying, which is every box for the first
# minutes after it is installed. It is protected already - the write goes to
# both - so this is an [ ok ] that says what is still happening.
mdstat <<'MD'
Personalities : [raid1]
md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]
      [====>................]  resync = 20.0% (418816/2094080) finish=0.2min speed=41881K/sec

unused devices: <none>
MD
run
if said '\[ ok \] records mirrored on 2 disks \(vdb1 vda3\), synchronising 20.0%'; then
  ok "a fresh mirror still copying reads as protected, and says it is copying"
else
  bad "an initial resync: $(printf '%s\n' "$OUTPUT" | grep -i mirror || echo '<nothing>')"
fi

# Two arrays, and the degraded one is NOT the records. A check that grepped
# /proc/mdstat for "[2/1]" would answer this box with somebody else's disk.
mdstat <<'MD'
Personalities : [raid1]
md1 : active raid1 vdd1[0]
      1046528 blocks super 1.2 [2/1] [U_]

md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
run
if said '\[ ok \] records mirrored on 2 disks \(vdb1 vda3\)'; then
  ok "another array being degraded is not this one"
else
  bad "a second array confused the records check: $(printf '%s\n' "$OUTPUT" | grep -i mirror || echo '<nothing>')"
fi

# The array that never assembled. fstab mounts the records nofail, so the box
# comes up with the volume missing and writes them to the root filesystem -
# and the check above cannot see it, because there is no md device to look at.
# What knows is the box's own record of the disks it was installed with.
echo /dev/vda2 > "$FAKE/mnt_records"
mkdir -p "$FAKE/root/etc/mdadm"
printf 'DEVICE partitions\nHOMEHOST <system>\nARRAY /dev/md0 metadata=1.2 name=cgnat01:records UUID=1234\n' \
  > "$FAKE/root/etc/mdadm/mdadm.conf"
mdstat <<'MD'
Personalities : [raid1]
unused devices: <none>
MD
run
if said '\[FAIL\] this box was installed with a records mirror and is not using it'; then
  ok "a mirror that is configured and not running is a FAILED check"
else
  bad "the mirror never assembled and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'mirror\|mdadm' || echo '<nothing>')"
fi

# --- and the box with ONE disk, which is most of them ---
#
# THE assertion of this section. Whatever the mirror costs, it must cost a
# one-disk box nothing at all: no line, no warning, and above all no failed
# check for hardware nobody bought.
rm -rf "$FAKE/root/etc/mdadm"
rm -f "$FAKE/root/proc/mdstat"
echo /dev/vda3 > "$FAKE/mnt_records"
run
if said 'mirror'; then
  bad "a box with one disk was told about mirrors: $(printf '%s\n' "$OUTPUT" | grep -i mirror)"
else
  ok "a box with one disk is told nothing about a mirror it does not have"
fi
if said '\[ ok \] records on their own filesystem: /dev/vda3'; then
  ok "and still reports its records volume exactly as before"
else
  bad "the one-disk records line changed: $(printf '%s\n' "$OUTPUT" | grep -i 'records on' || echo '<nothing>')"
fi
# The stock mdadm.conf every Debian box carries once mdadm is installed names
# no array. A check keyed on the file existing would fail every one-disk box
# in the field the day the medium started carrying mdadm.
mkdir -p "$FAKE/root/etc/mdadm"
printf 'DEVICE partitions\nHOMEHOST <system>\nMAILADDR root\n' \
  > "$FAKE/root/etc/mdadm/mdadm.conf"
run
if said 'mirror'; then
  bad "the stock mdadm.conf made a one-disk box report a mirror"
else
  ok "and an mdadm.conf that names no array leaves it silent"
fi

rm -f "$FAKE/mnt_root" "$FAKE/mnt_records"
rm -rf "$FAKE/root"

echo "=== which NIC the dataplane may claim (#222, #123) ==="
# The failure this replaces was silent and cost the session it was found on:
# the box shipped HEXA_DATAPLANE_IF=eth0, which does not exist under
# predictable names, the tuner generated a DPDK stanza with no allowlist, and
# VPP claimed whatever NIC it found - the management one, on a single-NIC box.
#
# Four branches, and three of them look alike from outside: the safe first-boot
# profile, the deliberate no-NIC one, and the dangerous one. The first version
# of this check conflated two of them and failed every live medium, so each is
# asserted separately rather than as "it said something".
nicconf() {  # <extra lines for the startup.conf of an otherwise tuned box>
  mkcpus "1-3" "0-3" "1-3" 0 1 2 3
  printf '%s\n' "$1" >> "$FAKE/root/etc/vpp/startup.conf"
}
: > "$FAKE/vppctl_ok"
# The dataplane HAS an interface. A name in startup.conf used to be the whole
# check, so this section passed on a box whose dataplane had nothing but
# local0 - which is the state an operator reaches by doing what the warning
# above tells them to do, since nothing in the appliance binds the NIC (#283).
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1      up          9000/0/0/0     rx packets                  12
local0                            0     down          0/0/0/0
IFACE
nicconf 'dpdk {
  dev 0000:03:00.0
}'
run
if said '\[ ok \] dataplane NIC: 0000:03:00.0'; then
  ok "a named device the dataplane really has is reported, with its address"
else
  bad "a startup.conf naming a device the dataplane HAS said nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
# AND IT SAYS THE BOX CHOSE IT, which is what #378 changed and the one thing
# an operator cannot read off a PCI address. The fixture's tune.env names no
# card, so this box claimed by itself - and the sentence has to say so, or a
# card that appeared without being asked for reads as one somebody forgot
# they typed.
if said 'claimed by this box with nobody asked'; then
  ok "a card the box claimed by itself says so, and does not read as typed"
else
  bad "the box claimed a card unbidden and the console does not say so: "\
"$(printf '%s\n' "$OUTPUT" | grep -i 'dataplane NIC' || echo '<nothing>')"
fi

# THE OVERRIDE, which is the other half of the same sentence. A box told which
# card to use must not claim to have chosen it: that is the box an operator
# goes looking for a bug in when the card is not the one they meant.
nicconf 'dpdk {
  dev 0000:03:00.0
}'
# AFTER nicconf, because mkcpus() rebuilds the fake root from nothing: written
# before it, this file is deleted and the box under test is one that named no
# card - which is the box the case above already covers, so the check would
# have passed by measuring the wrong machine.
mkdir -p "$FAKE/root/etc/hexarouting"
printf 'HEXA_DATAPLANE_IF=ens18\n' > "$FAKE/root/etc/hexarouting/tune.env"
run
if said "named in the configuration as 'ens18'"; then
  ok "a card named by the configuration is reported as named, not as chosen"
else
  bad "a box told which card to take reported it as one it chose by itself: "\
"$(printf '%s\n' "$OUTPUT" | grep -A2 -i 'dataplane NIC' || echo '<nothing>')"
fi
if said 'claimed by this box with nobody asked'; then
  bad "a card named in tune.env was reported as one nobody asked for"
else
  ok "and it does not claim nobody asked"
fi
rm -f "$FAKE/root/etc/hexarouting/tune.env"

# MORE THAN ONE CARD, which no fixture in this file could describe before
# #378 because the tuner could name exactly one. A box claiming two and
# reporting one is a box forwarding on half its ports and reading as healthy.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1      up          9000/0/0/0     rx packets                  12
TenGigabitEthernet3/0/1           2      up          9000/0/0/0     rx packets                  30
local0                            0     down          0/0/0/0
IFACE
nicconf 'dpdk {
  dev 0000:03:00.0
  dev 0000:03:00.1
}'
run
if said 'dataplane NIC\(s\): claimed by this box with nobody asked'; then
  ok "a box that claimed two cards reports both, not the first of them"
else
  bad "a box claiming two cards did not say so: "\
"$(printf '%s\n' "$OUTPUT" | grep -A3 -i 'dataplane NIC' || echo '<nothing>')"
fi
if said '0000:03:00.1'; then
  ok "and the second card is named, so a missing one can be noticed"
else
  bad "the second claimed card is nowhere on the console, so an operator "\
"cannot tell a box holding two from one holding one: "\
"$(printf '%s\n' "$OUTPUT" | grep -A3 -i 'dataplane NIC' || echo '<nothing>')"
fi

# AND THE CLAIM IS NOT ENOUGH (#382). The same box, with the driver having
# refused to start the port: the dataplane still HOLDS the interface, so every
# check above stays green - including the two #378 ones just above, which
# report a card the box claimed - and it reports ready while it cannot pass a
# packet. Claimed and forwarding are different claims, which is why the two
# halves are one screen on the box and two cases here.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1   error          9000/0/0/0
local0                            0     down          0/0/0/0
IFACE
nicconf 'dpdk {
  dev 0000:03:00.0
}'
run
if said_across "\[FAIL\] the dataplane holds interface\(s\) its driver refused to start:.*TenGigabitEthernet3/0/0"; then
  ok "a port in error state is a FAILED check, on a box that is otherwise sound"
else
  bad "the box reported ready with its only interface in error state: $(printf '%s\n' "$OUTPUT" | grep -i 'error state\|refused to start' || echo '<nothing about it>')"
fi
# And a port that DID start must not be reported as one that did not: an alarm
# that is always on is one nobody reads.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1      up          1500/0/0/0     rx packets                  12
local0                            0     down          0/0/0/0
IFACE
nicconf 'dpdk {
  dev 0000:03:00.0
}'
run
if said 'refused to start'; then
  bad "a port that started was reported as one the driver refused: $(printf '%s\n' "$OUTPUT" | grep -i 'refused to start')"
else
  ok "and a port that started is not reported as one that did not"
fi
# Back to the one-card box the cases below were written against.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1      up          9000/0/0/0     rx packets                  12
local0                            0     down          0/0/0/0
IFACE

# THE STATE AN OPERATOR ACTUALLY REACHES. They read the no-pci warning above,
# name the interface as it tells them to, and the dataplane still has nothing -
# because nothing in this appliance binds the NIC or loads vfio-pci. The box
# used to answer that with `[ ok ] dataplane NIC`, so the one screen meant to
# find this reported the box as sound (#283).
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
IFACE
nicconf 'dpdk {
  dev 0000:03:00.0
}'
run
if said_across '\[FAIL\] the dataplane has no interface, and one is named 0000:03:00.0'; then
  ok "a named device the dataplane does NOT have is a FAILED check, not an ok"
else
  bad "a box whose dataplane has only local0 passed on the strength of a name "\
"in a file: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
# And it says WHICH of the three states it is, because they need different
# work and read identically otherwise. The fake root has no PCI tree, so this
# is the "no driver at all" arm.
if said 'no driver DPDK can use is loaded'; then
  ok "and it names what is missing rather than guessing at a likely cause"
else
  bad "it failed without saying why: $(printf '%s\n' "$OUTPUT" | grep -A4 -i 'no interface' || echo '<nothing>')"
fi

nicconf 'plugin dpdk_plugin.so { disable }'
run
if said '\[info\] DPDK is off until hugepages exist'; then
  ok "the first-boot profile is told apart from a box with no device named"
else
  bad "DPDK disabled was not recognised: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
if said '\[FAIL\] the dataplane may claim any NIC'; then
  bad "the first-boot profile was reported as dangerous, which is what failed every live medium once"
else
  ok "and it is not reported as the dangerous case"
fi
nicconf 'dpdk {
  no-pci
}'
run
# THE SAME no-pci STARTUP.CONF ON A BOX THAT HAS A CARD (#378), which is a
# different box needing opposite work and reads identically from the file
# alone. The box claimed nothing and it has hardware, so it declined: the only
# card it could have taken is the one it is reached on, and it could not work
# out how to stay reachable without it. Telling this operator "this box has no
# PCI network card" would send them looking for hardware that is fitted.
mkdir -p "$FAKE/root/sys/bus/pci/devices/0000:03:00.0"
echo "0x020000" > "$FAKE/root/sys/bus/pci/devices/0000:03:00.0/class"
run
if said 'this box has 1 card\(s\) and claimed'; then
  ok "a box that declined its only card is told that, not that it has none"
else
  bad "a box with a card that claimed nothing was told it has no card: "\
"$(printf '%s\n' "$OUTPUT" | grep -i -A3 'no dataplane interface' || echo '<nothing>')"
fi
# One row's worth: the message folds at 79 and "a gateway" is on the next
# line, so a needle spelling both halves matches nothing over a box saying
# exactly the right thing (#285).
if said 'could not read an address, a MAC and a'; then
  ok "and what it could not work out, which is the only actionable part"
else
  bad "it says the box claimed nothing without saying why: "\
"$(printf '%s\n' "$OUTPUT" | grep -i -A5 'no dataplane interface' || echo '<nothing>')"
fi
# AND BACK TO THE BOX THE CASES BELOW WERE WRITTEN AGAINST. `run` leaves its
# output in OUTPUT, so without re-running here the next three checks would be
# reading this box's console while describing the one before it - which is the
# same class of mistake as a fixture built after the root is rebuilt.
rm -rf "$FAKE/root/sys/bus/pci"
run

# NO-PCI IS NO LONGER "NOBODY HAS TYPED YET" (#378). The box claims every card
# it has but a reserve, and never the last one, so a startup.conf that claims
# nothing is a box with no PCI network card at all. The warning stays a
# warning - nothing is broken and the box is healthy - but what it says had to
# change with what it means.
if said '\[warn\] no dataplane interface: this box has no PCI network card'; then
  ok "a box with no card to claim is a warning, and says that is what it is"
else
  bad "no-pci said nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
# AND IT MUST NOT TELL THEM TO NAME A CARD (#378), which is what it did.
#
# That advice was right for as long as reaching here meant nobody had typed
# yet: #283 moved it out of an editor and into the VTY, and this check is what
# kept it there. Reaching here now means the box looked and found no card, and
# `dataplane interface <name>` is then a statement with no name that could
# satisfy it - the operator types it, the VTY refuses because the box has no
# such interface, and they have been sent round a loop by their own console.
#
# So the assertion is inverted rather than deleted. What #283 was protecting
# is protected still: the box must not send anybody to a file in Linux, which
# is the check below and is untouched.
if said "dataplane interface <name>"; then
  bad "a box with no card to claim was told to name one, which it cannot do: $(printf '%s\n' "$OUTPUT" | grep -A4 -i 'no dataplane interface' || echo '<nothing>')"
else
  ok "and it does not send them to name a card this box has not got"
fi
if said "show hardware"; then
  ok "and it names the screen that lists what this box can actually see"
else
  bad "it says the box has no card and does not say where to look: $(printf '%s\n' "$OUTPUT" | grep -A4 -i 'no dataplane interface' || echo '<nothing>')"
fi
# Named exactly, and not by the file: the worker cap really does live in that
# file and really is advised there, a few checks up, so a bare match on the
# filename would be red for a message that is right.
if said 'HEXA_DATAPLANE_IF'; then
  bad "the advice still sends the operator to a variable in a Linux file"
else
  ok "and it does not send them to an editor in Linux"
fi
if said '\[FAIL\] the dataplane may claim any NIC'; then
  bad "no-pci was reported as the dangerous case; it is the opposite of it"
else
  ok "and it is not the dangerous case either"
fi
# DPDK on, nothing named, no no-pci. This is the one that takes the management
# interface on the next boot.
nicconf 'dpdk {
  socket-mem 1024
}'
run
if said '\[FAIL\] the dataplane may claim any NIC it finds, including this box'; then
  ok "a DPDK stanza that names no device at all is a FAILED check"
else
  bad "the dangerous profile passed: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
if said 'management interface'; then
  ok "and says which NIC it is about to lose"
else
  bad "the failure does not say what is at stake"
fi
rm -rf "$FAKE/root"

echo "=== the three statements the whole report rests on (#222) ==="
# The version, the RAM and the instruction set. Nothing held any of them, and
# the first line of the report is the one a reader trusts most: an operator who
# says "I am running 0.66.0" is reading it off this line, and a box whose stamp
# is missing said so to nobody.
#
# The RAM thresholds are not decoration either. docs/appliance.md records the
# event: the ISO booted with 2 GB and the console said "Out of memory: Killed
# process (vpp)" with VPP holding 19 MB. The floor exists because of that.
hw() {  # <version> <memkb> <cpu flags>
  mkcpus "1-3" "0-3" "1-3" 0 1 2 3
  mkdir -p "$FAKE/root/proc" "$FAKE/root/usr/share/hexarouting"
  [ -n "$1" ] && printf '%s\n' "$1" > "$FAKE/root/usr/share/hexarouting/VERSION"
  printf 'MemTotal:       %s kB\n' "$2" > "$FAKE/root/proc/meminfo"
  printf 'model name\t: Synthetic CPU\nflags\t\t: %s\n' "$3" \
    > "$FAKE/root/proc/cpuinfo"
}
hw "9.9.9 (abc1234)" 8388608 "fpu sse4_2 avx2"
run
if said '\[ ok \] HexaRouting 9.9.9 \(abc1234\)'; then
  ok "the version stamp on the box is what the report names"
else
  bad "the version stamp was not reported: $(printf '%s\n' "$OUTPUT" | head -3)"
fi
if said '\[ ok \] 8192 MB of RAM'; then
  ok "and the RAM it has, in MB, on a box that has enough"
else
  bad "8 GB was not reported: $(printf '%s\n' "$OUTPUT" | grep -i 'RAM' || echo '<nothing>')"
fi
if said '\[ ok \] CPU has sse4_2 and avx2'; then
  ok "and the instruction set VPP needs, when it is there"
else
  bad "sse4_2 and avx2 present and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'CPU' || echo '<nothing>')"
fi
hw "" 8388608 "fpu sse4_2 avx2"
run
if said '\[FAIL\] no version stamp in /usr/share/hexarouting/VERSION'; then
  ok "a box with no version stamp is a FAILED check, not a blank line"
else
  bad "a box with no version stamp: $(printf '%s\n' "$OUTPUT" | head -3)"
fi
# 2 GB is the machine the OOM kill was observed on.
hw "9.9.9" 2097152 "fpu sse4_2 avx2"
run
if said '\[FAIL\] only 2048 MB of RAM: VPP will be killed by the OOM killer'; then
  ok "the RAM the dataplane was killed on is a FAILED check, and says why"
else
  bad "2 GB passed: $(printf '%s\n' "$OUTPUT" | grep -i 'RAM' || echo '<nothing>')"
fi
if said 'minimum recommended: 4 GB'; then
  ok "and names the floor rather than only refusing"
else
  bad "the RAM failure does not say what is enough"
fi
# Between the floor and comfortable. A box that boots and is tight is not a
# box to refuse, and calling it a failure would be an alarm on ordinary
# hardware.
hw "9.9.9" 3145728 "fpu sse4_2 avx2"
run
if said '\[warn\] 3072 MB of RAM: enough to boot, tight under load'; then
  ok "3 GB is a warning: enough to boot, and said so"
else
  bad "3 GB: $(printf '%s\n' "$OUTPUT" | grep -i 'RAM' || echo '<nothing>')"
fi
if said '\[FAIL\].*MB of RAM'; then
  bad "3 GB was refused; a box that boots and runs tight is not one to refuse"
else
  ok "and it is not a failed check"
fi
# The instruction set VPP is compiled for. Without it the dataplane does not
# fail to forward, it dies with SIGILL - which reads like a crash and sends
# whoever is looking at the packet path.
hw "9.9.9" 8388608 "fpu sse2"
run
# Only sse4_2 is required: VPP dies with SIGILL without it and merely runs
# slower without avx2, so the two are a refusal and a warning rather than one
# list. Asserted as the product draws that line, not as it might have been
# drawn.
if said '\[FAIL\] CPU lacks: sse4_2 - VPP requires it'; then
  ok "a CPU without sse4_2 is a FAILED check, naming what is missing"
else
  bad "a CPU without sse4_2 passed: $(printf '%s\n' "$OUTPUT" | grep -i 'CPU' || echo '<nothing>')"
fi
if said 'SIGILL'; then
  ok "and says how it will present, because it presents as a crash"
else
  bad "the CPU failure does not say what the operator will see"
fi
# avx2 alone is a performance statement, not a refusal: VPP runs without it.
hw "9.9.9" 8388608 "fpu sse4_2"
run
if said '\[warn\] CPU lacks avx2'; then
  ok "a CPU with sse4_2 and no avx2 is a warning about throughput"
else
  bad "missing avx2 alone: $(printf '%s\n' "$OUTPUT" | grep -i 'CPU' || echo '<nothing>')"
fi
if said '\[FAIL\] CPU lacks'; then
  bad "a CPU that VPP runs on was refused over avx2"
else
  ok "and not a refusal, because VPP runs on it"
fi
rm -rf "$FAKE/root"

echo "=== the dataplane answering, and not (#222) ==="
# The most basic statement in the report, and nothing held it. A box whose VPP
# is not answering has to say so at the top, because every line below it is
# then about a box that is not forwarding.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
: > "$FAKE/vppctl_ok"
run
if said '\[ ok \] VPP responding'; then
  ok "a dataplane that answers is reported at the top of the report"
else
  bad "a responding VPP was not reported: $(printf '%s\n' "$OUTPUT" | grep -i 'VPP' | head -2 || echo '<nothing>')"
fi
rm -f "$FAKE/vppctl_ok"
run
if said '\[FAIL\] VPP not responding'; then
  ok "and one that does not answer is a FAILED check"
else
  bad "a dead VPP passed: $(printf '%s\n' "$OUTPUT" | grep -i 'VPP' | head -2 || echo '<nothing>')"
fi
rm -rf "$FAKE/root"

echo "=== a box with no vpp group at all (#222) ==="
# The group is what makes the API shareable. Without it the only account that
# can configure a pool is root, and this box exists to be operated by people
# who are not root - which is the whole of #35 and #184. The branch had
# nothing holding it.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
: > "$FAKE/vppctl_ok"
printf 'Plugin path\n bpa_plugin.so\n' > "$FAKE/show_plugins"
printf 'frr:x:901:\nfrrvty:x:902:\nhexa-admin:x:903:\nhexa-cgnat:x:904:\n' \
  > "$FAKE/group"
: > "$FAKE/group.keep"
run
if said "\\[FAIL\\] there is no 'vpp' group, so the API cannot be shared"; then
  ok "a box with no vpp group is a FAILED check"
else
  bad "the vpp group was missing and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'vpp.*group' || echo '<nothing>')"
fi
rm -f "$FAKE/group.keep"

echo "=== the API reach, proven and disproven (#222) ==="
# The account really has the group and the call really is made as that account.
# Both outcomes matter: a reach that cannot be proven and a reach that was
# proven to fail are different states, and one of them was unheld.
printf 'root:x:0:0::/root:/usr/bin/hexash-login\nnoc:x:1000:1000::/home/noc:/usr/bin/hexash-login\n' \
  > "$FAKE/passwd"
printf 'noc hexa-admin hexa-cgnat vpp frr frrvty\n' > "$FAKE/groups"
echo 1 > "$FAKE/api_rc"
run
if said '\[FAIL\] the binary API does not work for noc'; then
  ok "an account that has the group and cannot reach the API is a FAILED check"
else
  bad "a failing API call passed: $(printf '%s\n' "$OUTPUT" | grep -i 'binary API' || echo '<nothing>')"
fi
if said 'must be group vpp, mode 660'; then
  ok "and it names the permission that has to be right"
else
  bad "the failure does not say what to look at"
fi
echo 0 > "$FAKE/api_rc"
run
if said '\[ ok \] the binary API works for noc'; then
  ok "and a working one is reported, naming the account it was proven with"
else
  bad "a working API was not reported: $(printf '%s\n' "$OUTPUT" | grep -i 'binary API' || echo '<nothing>')"
fi

echo "=== the service that applies the configuration (#222) ==="
# hexarouting.service is what puts the saved configuration into the dataplane
# at boot. When it fails the box comes up forwarding nothing it was configured
# to forward, while VPP, FRR and the VTY all answer perfectly. The stub
# answered `inactive` for every run, so this branch had never executed.
echo failed > "$FAKE/is_failed"
printf 'bpa_pool_add_del: pool overlaps an existing one\n' \
  > "$FAKE/journal_hexarouting"
run
if said '\[FAIL\] hexarouting.service FAILED: the CGNAT configuration was not applied'; then
  ok "a failed configuration service is a FAILED check"
else
  bad "hexarouting.service failed and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i hexarouting.service || echo '<nothing>')"
fi
# "Failed" without a reason sends whoever is standing there to a journal they
# may not be able to reach yet, which is why the check prints the last six
# lines of it. They have to be this box's.
if said 'pool overlaps an existing one'; then
  ok "and the last thing it said is on the screen with it"
else
  bad "the unit failed and the reason was not printed, so the console says \
the configuration was not applied and not what refused it: $(printf '%s\n' "$OUTPUT" | grep -A3 -i 'the last thing it said' | tr '\n' ' ')"
fi
rm -f "$FAKE/is_failed" "$FAKE/journal_hexarouting"
run
if said '\[FAIL\] hexarouting.service FAILED'; then
  bad "a box whose configuration service is fine was reported as failed"
else
  ok "and a box where it is fine is not told about it"
fi

echo "=== how many pools the box ended up carrying (#222) ==="
# The last line of the report, and the one an operator reads to see whether
# what they configured is on the box at all.
printf 'pool residential\n  mode deterministic\npool business\n  mode dynamic\n' \
  > "$FAKE/bpa_pools"
run
if said '\[ ok \] 2 CGNAT pool\(s\) configured'; then
  ok "the pools on the box are counted and reported"
else
  bad "two pools were configured and the report did not say so: $(printf '%s\n' "$OUTPUT" | grep -i 'pool' | tail -2 || echo '<nothing>')"
fi
rm -f "$FAKE/bpa_pools"
run
# Zero pools is a real state on a box somebody has just installed, and it has
# to read as zero rather than as silence - but it is an [info] and not a
# failure, because a box nobody has configured yet is not a broken box.
if said '\[info\] no CGNAT pool configured yet'; then
  ok "and a box with none says so, as information rather than as a fault"
else
  bad "a box with no pools said nothing about pools: $(printf '%s\n' "$OUTPUT" | grep -i 'pool' | tail -1 || echo '<nothing>')"
fi
if said '\[FAIL\].*CGNAT pool'; then
  bad "a box nobody has configured yet was reported as broken"
else
  ok "and it is not a failed check"
fi
rm -f "$FAKE/show_plugins"
rm -rf "$FAKE/root"

echo "=== the pools the box was told about, against the ones it has (#172) ==="
# The same comparison the IPFIX block already makes, for the object this
# product exists for. A pool in the saved file that the dataplane refused is
# applied by nobody and reported by nothing: hexarouting.service succeeds
# because the rest of the file applied, and the count above says how many pools
# are there rather than how many are missing. Every subscriber behind that pool
# is untranslated, on a box that boots and forwards other people's traffic.
mkcpus "1-3" "0-3" "1-3" 0 1 2 3
: > "$FAKE/vppctl_ok"
printf 'Plugin path\n bpa_plugin.so\n' > "$FAKE/show_plugins"
# HEXA_CONFIG was unset by the IPFIX section above, which is right for the
# sections between the two: without it the self-test reads the real box's
# /etc/hexarouting/hexarouting.conf, and this check would then compare a
# developer's own pools against the fake dataplane. Set again here, and unset
# again at the end, for the same reason it was unset there.
export HEXA_CONFIG="$FAKE/hexarouting.conf"
printf 'nat cgnat pool alpha\n inside 100.64.0.0/24\n exit\nnat cgnat pool beta\n inside 100.64.1.0/24\n exit\n' \
  > "$FAKE/hexarouting.conf"
# The dataplane's own line shape: "pool <name> [<mode> / <policy>]".
printf 'pool alpha [deterministic / interleaved]\npool beta [dynamic / blocked]\n' \
  > "$FAKE/bpa_pools"
run
if said '\[ ok \] and every pool the saved configuration names is on the dataplane'; then
  ok "a box whose pools all applied is reported so"
else
  bad "both pools were there and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i 'pool' | tail -2 || echo '<nothing>')"
fi
# The pool the dataplane refused. Same file, one pool missing from the box.
printf 'pool alpha [deterministic / interleaved]\n' > "$FAKE/bpa_pools"
run
if said '\[FAIL\] the saved configuration has pool\(s\) beta and the dataplane'; then
  ok "a pool in the file and not on the box is a FAILED check, and it is named"
else
  bad "a refused pool passed: $(printf '%s\n' "$OUTPUT" | grep -i 'pool' | tail -2 || echo '<nothing>')"
fi
# The sentence wraps across two say() lines, so the needle is the half that
# is on one of them. A needle that spans a line break can never match, which
# is the first thing to check when an assertion about a message goes red.
if said 'subscriber behind them is untranslated'; then
  ok "and the failure says what it costs, in subscribers"
else
  bad "the failure does not say what a missing pool means"
fi
if said 'journalctl -u'; then
  ok "and where the dataplane's own reason is"
else
  bad "the failure does not say where to find the reason"
fi
# A box with no pools in the file at all must say nothing here: an operator who
# has not configured a pool yet is not a box with a defect, and the [info]
# above already covers them.
printf '\n' > "$FAKE/hexarouting.conf"
rm -f "$FAKE/bpa_pools"
run
if said 'saved configuration has pool'; then
  bad "a box with no pools configured was told a pool is missing"
else
  ok "a box that has configured no pool is not told one is missing"
fi

echo "=== uRPF: on in the file, and on in the dataplane (#172) ==="
# More at stake than the pools. uRPF is what stops a spoofed source being
# translated, and therefore what stops a packet nobody sent being attributed to
# a subscriber in the retention log - a record indistinguishable from a true
# one. A box whose saved configuration turns it on and whose dataplane does not
# have it forwards, translates, logs and answers everything, and there is no
# counter anywhere for a protection that is simply absent.
printf 'nat cgnat pool alpha\n exit\ninterface TenGigabitEthernet0/0/0\n urpf strict\n exit\n' \
  > "$FAKE/hexarouting.conf"
printf 'pool alpha [deterministic / interleaved]\n' > "$FAKE/bpa_pools"
printf 'TenGigabitEthernet0/0/0 strict\n' > "$FAKE/bpa_urpf"
run
if said '\[ ok \] uRPF is on in the dataplane, as the saved configuration asks'; then
  ok "a box whose uRPF really is on is reported so, with what the box has"
else
  bad "uRPF was on in both and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i urpf || echo '<nothing>')"
fi
# The one that matters: the file asks for it and the dataplane has it nowhere.
printf 'no interface has uRPF enabled\n' > "$FAKE/bpa_urpf"
run
if said '\[FAIL\] the saved configuration turns uRPF on and the dataplane has it on'; then
  ok "uRPF asked for and absent is a FAILED check"
else
  bad "a box with no uRPF at all passed: $(printf '%s\n' "$OUTPUT" | grep -i urpf || echo '<nothing>')"
fi
if said 'attributed'; then
  ok "and the failure says what it costs, which is a record about the wrong subscriber"
else
  bad "the failure does not say what an absent uRPF means"
fi
# A box that never asked for uRPF is told nothing: most boxes do not run it,
# and an alarm that is always on is one nobody reads.
printf 'nat cgnat pool alpha\n exit\n' > "$FAKE/hexarouting.conf"
run
if said 'uRPF'; then
  bad "a box that never configured uRPF was told about it: $(printf '%s\n' "$OUTPUT" | grep -i urpf | head -1)"
else
  ok "a box that never asked for uRPF is not told about it"
fi
rm -f "$FAKE/bpa_urpf"

echo "=== the addresses in the file, against the ones the dataplane has (#172) ==="
# The one that can leave the box unreachable. An address the dataplane did not
# take is not a degraded box: it answers on one fewer address than whoever
# configured it believes, and if that was the management address nobody can
# reach it to find out.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet0/0/0           2      up          1500/0/0/0     rx packets                  12
host-veth-vpp                     1      up          1500/0/0/0
IFACE
cat > "$FAKE/show_interface_addr" <<'ADDR'
TenGigabitEthernet0/0/0 (up):
  L3 200.150.0.1/24
host-veth-vpp (up):
  L3 10.0.0.1/30
ADDR
printf 'interface TenGigabitEthernet0/0/0\n ipv4 address 200.150.0.1/24\n exit\ninterface host-veth-vpp\n ipv4 address 10.0.0.1/30\n exit\n' \
  > "$FAKE/hexarouting.conf"
run
if said 'address\(es\) the dataplane does not'; then
  bad "both addresses were there and the box complained: $(printf '%s\n' "$OUTPUT" | grep -i 'address(es)')"
else
  ok "a box whose addresses all took is not told about them"
fi
# The one that matters: the file has an address the dataplane never got.
printf 'interface TenGigabitEthernet0/0/0\n ipv4 address 200.150.0.1/24\n ipv4 address 203.0.113.1/24\n exit\n' \
  > "$FAKE/hexarouting.conf"
run
if said_across '\[FAIL\] the saved configuration has address\(es\) the dataplane does not: TenGigabitEthernet0/0/0:203.0.113.1/24'; then
  ok "an address the dataplane did not take is a FAILED check, named with its interface"
else
  bad "a refused address passed: $(printf '%s\n' "$OUTPUT" | grep -i 'address' | head -2 || echo '<nothing>')"
fi
# The sentence wraps across two say() lines, so the needle is the half that
# is on one of them - a needle that spans a line break can never match.
if said 'can reach it to find out'; then
  ok "and the failure says what the worst case is"
else
  bad "the failure does not say what a missing address costs"
fi
# The address is on the box but on the WRONG interface. Matching it anywhere in
# the output would call this correct, which is a box whose management address
# answers on the subscriber-facing port.
cat > "$FAKE/show_interface_addr" <<'ADDR'
TenGigabitEthernet0/0/0 (up):
  L3 200.150.0.1/24
host-veth-vpp (up):
  L3 203.0.113.1/24
ADDR
run
if said 'TenGigabitEthernet0/0/0:203.0.113.1/24'; then
  ok "an address that landed on another interface is still missing from this one"
else
  bad "an address on the wrong interface was accepted: $(printf '%s\n' "$OUTPUT" | grep -i 'address(es)' || echo '<nothing>')"
fi
rm -f "$FAKE/show_interface_addr"

echo "=== the MTU in the file, against the MTU the dataplane has (#172) ==="
# The mismatch this project has already paid for twice. An interface whose
# saved mtu the dataplane did not take still forwards; what then happens is
# that an OSPF adjacency will not leave Exchange, because the two ends
# advertise different MTUs and NEITHER END LOGS ANYTHING. Until now the only
# thing that noticed was a finding at commit time, which says nothing on a box
# that was rebooted.
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet0/0/0           2      up          1500/0/0/0     rx packets                  12
host-veth-vpp                     1      up          9000/0/0/0
IFACE
printf 'interface TenGigabitEthernet0/0/0\n mtu 1500\n exit\ninterface host-veth-vpp\n mtu 9000\n exit\n' \
  > "$FAKE/hexarouting.conf"
run
if said 'the saved MTU is not the MTU the dataplane has'; then
  bad "two interfaces at the MTU they were told and the box complained: $(printf '%s\n' "$OUTPUT" | grep -i 'saved MTU')"
else
  ok "a box whose MTUs all took is not told about them"
fi
# The one that matters: the file says 9000 and the dataplane kept 1500. This is
# the box that forwards and whose OSPF never comes up.
printf 'interface TenGigabitEthernet0/0/0\n mtu 9000\n exit\n' \
  > "$FAKE/hexarouting.conf"
run
if said_across '\[FAIL\] the saved MTU is not the MTU the dataplane has: TenGigabitEthernet0/0/0\(9000/1500\)'; then
  ok "an MTU the dataplane did not take is a FAILED check, naming both numbers"
else
  bad "a refused MTU passed: $(printf '%s\n' "$OUTPUT" | grep -i mtu || echo '<nothing>')"
fi
if said 'stops at Exchange'; then
  ok "and the failure names the symptom an operator will actually meet"
else
  bad "the failure does not say what a wrong MTU costs"
fi
# An interface in the file that the box does not have at all is the pair
# check's business, not this one: saying it twice, in two different ways, is
# how a reader learns to skip both.
printf 'interface TenGigabitEthernet9/9/9\n mtu 9000\n exit\n' \
  > "$FAKE/hexarouting.conf"
run
if said 'the saved MTU is not the MTU'; then
  bad "an interface the box does not have was reported as an MTU mismatch"
else
  ok "an interface the box does not have is left to the check that is about that"
fi

rm -f "$FAKE/show_plugins" "$FAKE/bpa_pools"
printf '\n' > "$FAKE/hexarouting.conf"
unset HEXA_CONFIG
rm -rf "$FAKE/root"

# A box's ssh host keys: made here, or handed to it inside the image?
#
# Every ISO published up to 0.66.0 carried the host keys its build container
# generated, private halves included, and hexarouting-install unpacked them
# onto the disk. Those boxes are still racked and still answering with a key
# anybody who downloaded the medium holds (#248). #245 fixed the image and
# every install after it; this is what tells the ones already in the field.
#
# The signal is the timestamp and NOT the key's comment. `ssh-keygen -A`
# stamps "<user>@<gethostname()>", and chroot does not change gethostname():
# the installer runs it as `chroot "$MNT" ssh-keygen -A`, so a correctly
# generated key on a box named anything at all carries the LIVE MEDIUM's
# hostname, measured as "root@hexarouting". A comment-against-hostname check
# would report every correctly installed box that was given a name.
#
# What is left is that a file cannot have contents older than itself: a key
# generated here has an mtime at or after the moment its inode was made on
# this filesystem, and one restored from a squashfs carries the mtime the
# build container wrote onto an inode created at install time.
#
# Both timestamps come from the stubbed stat, so a case is a pair of numbers
# rather than a filesystem feature of whatever /tmp is.
mkkeys() {  # <mtime> <birth> - three host keys with those timestamps
  R="$FAKE/root"
  rm -rf "$R"; mkdir -p "$R/etc/ssh"
  : > "$FAKE/keytimes"
  for _kt in ecdsa ed25519 rsa; do
    : > "$R/etc/ssh/ssh_host_${_kt}_key"
    [ -n "${1:-}" ] && printf 'ssh_host_%s_key %s %s\n' "$_kt" "$1" "$2" \
      >> "$FAKE/keytimes"
  done
}

echo "=== ssh host keys that came in with the image ==="
# mtime is the 0.37.0 medium's own: its keys are stamped 2026-09-08 13:43,
# three minutes before its squashfs was made. The birth time is the install,
# six days later.
mkkeys 1757349810 1789000000
run
if said '\[FAIL\] the ssh host keys were not generated on this box'; then
  ok "it is a failed check, not a warning"
else
  bad "the handed-down key was not reported as a failure: $(printf '%s\n' "$OUTPUT" | grep -i "host key" || echo '<nothing about host keys>')"
fi
# The keys by name, on a row of their own: an operator who has to decide
# whether to regenerate wants to see which files are being talked about.
if said '^ *ssh_host_ecdsa_key ssh_host_ed25519_key ssh_host_rsa_key$'; then
  ok "and it names the key files, on a row of their own"
else
  bad "it did not name the key files on a row of their own"
fi
# WHY it matters, which is the half a bare "regenerate your keys" leaves out.
if said_across 'private half of it is in the published download'; then
  ok "and says the private half is in the download"
else
  bad "it did not say the private half is published"
fi
if said_across 'impersonated, and the client will not object, because the fingerprint it pinned is the fingerprint it was handed'; then
  ok "and says an ssh session can be impersonated with nothing to notice"
else
  bad "it did not say the session can be impersonated unnoticed"
fi
# What to do, typeable as it stands.
if said '^ *rm /etc/ssh/ssh_host_\*_key /etc/ssh/ssh_host_\*_key\.pub$' \
   && said '^ *ssh-keygen -A$' && said '^ *systemctl restart ssh$'; then
  ok "and gives the three commands that fix it, one per row"
else
  bad "it did not give the remediation as typeable commands: $(printf '%s\n' "$OUTPUT" | grep -c 'ssh-keygen') ssh-keygen line(s)"
fi
# And the cost of doing it, because an operator surprised by a changed
# fingerprint assumes they are being attacked.
if said_across 'pinned the old fingerprint will then refuse to connect'; then
  ok "and warns that every client pinning the old fingerprint will object"
else
  bad "it did not warn that clients will refuse the next connection"
fi

echo "=== ssh host keys this box generated itself ==="
# The other direction, and the one a check like this gets wrong: a box whose
# keys are its own must not be accused. ssh-keygen creates the file and writes
# it, so mtime is the birth time or a second past it when the write straddles
# a second boundary.
for _mt in 1789000000 1789000001; do
  mkkeys "$_mt" 1789000000
  run
  if said 'the ssh host keys were not generated on this box'; then
    bad "a box whose keys are its own was accused (mtime $_mt)"
  else
    ok "it is not accused (mtime $_mt, birth 1789000000)"
  fi
done
if said '\[ ok \] the ssh host keys were generated on this box'; then
  ok "and the box is told so in as many words"
else
  bad "a box with its own keys was told nothing about them"
fi

echo "=== one key of three came in with the image ==="
# A box remediated by hand can end up here: `ssh-keygen -A` only makes the
# types that are missing, so a leftover key from the image survives a partial
# cleanup. The box is still impersonable over whichever type the client picks.
mkkeys 1789000000 1789000000
sed -i 's/^ssh_host_rsa_key .*/ssh_host_rsa_key 1757349810 1789000000/' \
  "$FAKE/keytimes"
run
if said '\[FAIL\] the ssh host keys were not generated on this box'; then
  ok "one key out of three is still a failed check"
else
  bad "a box with one image key among its own was passed"
fi
if said '^ *ssh_host_rsa_key$'; then
  ok "and only that key is named"
else
  bad "it did not name the one key that came from the image: $(printf '%s\n' "$OUTPUT" | grep -E '^ *ssh_host' || echo '<no key row>')"
fi

echo "=== a box with no ssh host keys at all ==="
# The check must not be blind. An installed box with no host key is a box
# whose sshd will not start, and a silent check there would read as "these
# are fine" - which is the failure mode the whole check exists to avoid.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/etc/ssh"; rm -f "$FAKE/keytimes"
run
if said '\[ ok \] the ssh host keys were generated on this box'; then
  bad "a box with no host keys at all read as a box with good ones"
else
  ok "it does not read as a box whose keys are fine"
fi
if said '\[FAIL\] no ssh host keys in /etc/ssh: sshd cannot start'; then
  ok "and says sshd cannot start without them"
else
  bad "a box with no host keys was not reported: $(printf '%s\n' "$OUTPUT" | grep -i "host key" || echo '<nothing about host keys>')"
fi
# And the repair for THAT box, which is not the other one: there is nothing to
# remove, and an operator typing a removal at the console of a box whose sshd
# will not start is deleting from the only way back in. Rendered from the
# holder's own rows rather than written out here.
if said '^ *ssh-keygen -A$' && said '^ *systemctl restart ssh$' \
   && ! said 'rm /etc/ssh/ssh_host'; then
  ok "and gives the two commands that make a set, without the removal"
else
  bad "the no-keys repair is not the holder's: $(printf '%s\n' "$OUTPUT" | grep -E 'ssh-keygen|systemctl restart ssh|rm /etc/ssh' | tr '\n' ' ')"
fi

echo "=== and a box where the holder cannot be asked at all ==="
# A check that could not run reads exactly like a check that agreed, and what
# this one is about is whether an ssh session to this box can be impersonated.
# The self-test is not the banner: its job is to report on the box, so a
# question it could not put is a finding and it says so.
#
# Arranged by running a copy of the self-test from a directory with no holder
# beside it, which is how it would look on a box the installer reached with an
# older manifest - the self-test finds the holder with `dirname "$0"`.
mkdir -p "$FAKE/nohold"
cp "$REPO/appliance/selftest" "$FAKE/nohold/selftest"
ST="$FAKE/nohold/selftest"
run
ST="$REPO/appliance/selftest"
if said '\[FAIL\] cannot tell where the ssh host keys were made'; then
  ok "it is a failed check rather than silence"
else
  bad "a self-test that could not ask said nothing about it: $(printf '%s\n' "$OUTPUT" | grep -i "host key" || echo '<nothing about host keys>')"
fi
# The path, on a row of its own, because it is the one thing that turns this
# into something somebody can act on rather than a fact about the box.
if said "^ *$FAKE/nohold/ssh-host-key-origin$"; then
  ok "and it names the program that did not answer, whole and on its own row"
else
  bad "it did not say which program it could not run: $(printf '%s\n' "$OUTPUT" | grep -A 6 -i 'cannot tell where the ssh host keys' | tail -2 || echo '<no such report>')"
fi
if said '\[ ok \] the ssh host keys were generated on this box'; then
  bad "a box nothing could be measured on was passed"
else
  ok "and it does not read as a box whose keys are fine"
fi

echo "=== a live medium, which carries no host keys on purpose ==="
# #245 removed them from the image and masked sshd there. Reporting that as a
# failure would fail a healthy medium on every boot, and a self-test that
# cries wolf on the ISO is one whose failures get read past.
mkdir -p "$FAKE/root/run/live/medium"
run
if said 'no ssh host keys in /etc/ssh'; then
  bad "the live medium was failed for carrying no host keys, which is #245"
else
  ok "the live medium is not failed for it"
fi
rm -rf "$FAKE/root/run"

echo "=== a key whose timestamps cannot be read ==="
# Two ways to get here: a filesystem that records no birth time (stat answers
# 0), and a file stat could not answer for at all. Neither is a pass. A check
# that cannot see must not be mistaken for a check that agrees.
mkkeys 1789000000 0
run
if said '\[ ok \] the ssh host keys were generated on this box'; then
  bad "keys with no birth time read as keys that were generated here"
else
  ok "no birth time does not read as a pass"
fi
if said 'cannot tell where these ssh host keys were made'; then
  ok "and it says so, rather than staying silent"
else
  bad "it said nothing at all about keys it could not judge"
fi
mkkeys 1789000000 1789000000
sed -i '/^ssh_host_rsa_key /d' "$FAKE/keytimes"   # stat answers nothing for it
run
if said '\[ ok \] the ssh host keys were generated on this box'; then
  bad "a key stat could not read was counted among keys that are fine"
else
  ok "a key stat could not read does not read as a pass either"
fi
if said '^ *ssh_host_rsa_key$'; then
  ok "and it names the key it could not judge"
else
  bad "it did not name the unreadable key"
fi

echo "=== and this file decides none of it: it renders what the holder says ==="
# WHERE THE RULE WENT, and why this assertion replaced the one that used to be
# here. The mtime-against-birth-time rule, the rejected alternatives and the
# repair all live in appliance/ssh-host-key-origin now, because the VTY banner
# has to reach the same answer for an operator who logged in over ssh - and
# that operator is the one the answer is about (#248). Everything about the
# signal itself, including the .pub and hostname reading that would accuse a
# correctly installed box, is asserted against the holder in
# test/test_ssh_host_key_origin.py.
#
# What is left for this file to hold is that it did not keep a copy. A rule
# implemented twice is a rule that can disagree with itself about whether an
# operator is being impersonated, and the copy would go on passing every
# assertion above it.
DECIDES=$(sed -n '/were these ssh host keys made on this box/,/A live system says so/p' \
            "$REPO/appliance/selftest" \
          | grep -vE '^ *(#|say |say_list |bad |ok |\"|\\\\)' \
          | grep -nE 'stat |%W|%Y' || true)
if [ -z "$DECIDES" ]; then
  ok "it reads no timestamp of its own: the rule has one holder"
else
  bad "the self-test decides this itself as well as the holder, so the two can disagree: $DECIDES"
fi
# On a line that RUNS, not in the paragraph above it that explains where the
# rule went: a comment naming the holder is what a copy of the rule would be
# left sitting under.
if sed -n '/were these ssh host keys made on this box/,/A live system says so/p' \
     "$REPO/appliance/selftest" | grep -vE '^ *#' | grep -q 'ssh-host-key-origin'; then
  ok "and it names the holder it renders"
else
  bad "the self-test reaches this answer without running appliance/ssh-host-key-origin"
fi

echo "=== a box with nothing wrong with it, which is every box in the field (#298) ==="
# THE CONTROL, and the reason it is worth the screen it takes.
#
# Every fixture above exists to make something go wrong, which is the right
# instinct: a check is proven by the fault it catches. The other half was
# missing. Until this box the suite's own count line read "105 of 105 runs
# arranged a box with failed checks", so the three summary branches under
# `fail = 0` - the last line every healthy appliance in the field prints - had
# nothing behind them at all, and neither did any other behaviour that appears
# only when nothing is wrong.
#
# #297 found that out the hard way. Its assertion was driven through the
# no-pci box, came out green, and then STAYED green under both mutations of
# the thing it was meant to test: that box carries eight failures of its own,
# so "the summary does not say ready" was true whatever the summary did. A
# check that cannot go red is worse than no check, and the only reason that
# one is not in the tree is that somebody ran the mutations.
#
# lab/nat-behaviour.sh already makes this argument for the labs - it runs in a
# mode where its full-cone assertions MUST fail, because an assertion that
# passes under both conditions is not testing anything. This is that mode for
# the self-test, the other way up.
#
# It is also the only place in the tree that says, in one screen, what a
# working appliance looks like: a box this file finds NOTHING wrong with has
# to answer every check in it at once.

# hexash, which no box above has and none of them should.
#
# `command -v hexash` is the first thing the VTY check does, and the machine
# running this suite has no hexash - so all 105 boxes above say "[FAIL] hexash
# is not installed". Putting this stub in $BIN at the top of the file would
# quietly repair a failed check on every one of them, and those boxes are not
# this issue's to change: this adds a control, it does not soften a fault. So
# the front door is stubbed here and taken away again at the end of the
# section.
cat > "$BIN/hexash" <<'EOF'
#!/bin/sh
echo "no rollback point saved yet"
EOF
chmod +x "$BIN/hexash"

# What the dataplane reports about the allocation log, with the TCP transport
# in a state the caller names. ONE producer for every TCP case below, because
# the only thing under test is which word is in the collector line: three
# fixtures written out by hand would let the lines around it drift and turn a
# difference in the state word into a difference in something else.
#
# Copied field for field from bpa_show_logging_command_fn() in
# src/plugins/bpa/bpa_cli.c, spacing included, because the self-test reads the
# collector line with a sed anchored on that spacing.
bpa_logging_tcp() {  # <state word> [collector, default 10.0.0.2:4739]
  cat > "$FAKE/bpa_logging" <<LOG
IPFIX export of block events (RFC 8158)
  state       enabled
  exporter    10.0.0.2:4739 src 10.0.0.1 (path mtu 512)
  records     0 emitted, 0 dropped
TCP transport (survives collector outage)
  collector   ${2:-10.0.0.2:4739}  $1
  records     12 emitted, 4096 bytes sent, 0 pending
  spill       0 bytes on disk, 0 replayed, 0 dropped
  connections 1, service 42, send 3, errno 0
LOG
}

# A box the self-test finds nothing wrong with, arranged in the order the
# self-test reads it. The order is not decoration: a check added there without
# a fixture added here shows up as this box going red, which is the whole
# value of keeping one box that has to satisfy all of them.
healthy() {
  # The cores. FOUR left over for the control plane and not one: one cpu left
  # while more than one is isolated is a warning in its own right, and the
  # point of this box is that it has none.
  mkcpus "4-7" "0-7" "4-7" 0 4 5 6 7
  R="$FAKE/root"
  mkdir -p "$R/usr/share/hexarouting" "$R/proc" "$R/etc/ssh" \
           "$R/var/log/hexarouting" "$R/sys/devices/system/cpu/cpuidle" \
           "$R/sys/devices/system/cpu/cpufreq/policy0"
  printf '0.71.0 (a1b2c3d)\n' > "$R/usr/share/hexarouting/VERSION"
  printf 'MemTotal:       8388608 kB\n' > "$R/proc/meminfo"
  printf 'model name\t: Synthetic CPU\nflags\t\t: fpu sse4_2 avx2\n' \
    > "$R/proc/cpuinfo"
  # What this boot GOT, and what the kernel exposes for it to act on. Both,
  # because a parameter the kernel ignored leaves /proc/cmdline saying it was
  # asked for and the kernel behaving as though it was not.
  echo "BOOT_IMAGE=/vmlinuz isolcpus=4-7 processor.max_cstate=1" \
    > "$R/proc/cmdline"
  echo intel_idle > "$R/sys/devices/system/cpu/cpuidle/current_driver"
  echo performance > "$R/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"
  # The NIC the tuner was told to claim, and a statistics segment somebody
  # chose the size of.
  printf 'dpdk {\n  dev 0000:03:00.0\n}\nstatseg {\n  size 256M\n}\n' \
    >> "$R/etc/vpp/startup.conf"
  # The records, on their own volume and on the two disks #83 is about.
  echo /dev/vda2 > "$FAKE/mnt_root"
  echo /dev/md0 > "$FAKE/mnt_records"
  cat > "$R/proc/mdstat" <<'MD'
Personalities : [raid1]
md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
  # Host keys whose contents are not older than the inode they sit on, which
  # is what a key generated on this box looks like and what a key that came in
  # with the system image does not (#248).
  : > "$FAKE/keytimes"
  for _kt in ecdsa ed25519 rsa; do
    : > "$R/etc/ssh/ssh_host_${_kt}_key"
    printf 'ssh_host_%s_key 1789000000 1789000000\n' "$_kt" >> "$FAKE/keytimes"
  done
  # The dataplane: answering, with the plugin, out of the OOM killer's
  # selection, holding the NIC that startup.conf names, and paired into the
  # kernel so that FRR can see the port the traffic is on.
  : > "$FAKE/vppctl_ok"
  echo -1000 > "$FAKE/oom_score_adj"
  printf 'Plugin path\n bpa_plugin.so\n' > "$FAKE/show_plugins"
  cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
TenGigabitEthernet3/0/0           1      up          9000/0/0/0     rx packets                  12
local0                            0     down          0/0/0/0
tap0                              2      up          9000/0/0/0
IFACE
  cat > "$FAKE/show_interface_addr" <<'ADDR'
TenGigabitEthernet3/0/0 (up):
  L3 200.150.0.1/24
ADDR
  printf 'itf-pair: [0] TenGigabitEthernet3/0/0 tap0 wan0 3 type tap\n' \
    > "$FAKE/show_lcp"
  cat > "$FAKE/statseg" <<'SEG'
Entry: /sys/vector_rate (type 2)
  total: 31.99M, used: 4.20M, free: 27.79M, trimmable: 27.79M
SEG
  printf 'pool alpha [dynamic / blocked]\n' > "$FAKE/bpa_pools"
  printf 'TenGigabitEthernet3/0/0: strict\n' > "$FAKE/bpa_urpf"
  printf ' mode dynamic\n' > "$FAKE/bpa_config"
  # The TCP transport CONNECTED, which no box in this suite had: every fixture
  # ended "TCP transport: disabled", so the collector line was never produced
  # and neither arm of the check that reads it had ever run (#300).
  bpa_logging_tcp connected
  # FRR answering, with every daemon the VTY offers a protocol for (#187) and
  # the module bgpd refuses to start without.
  printf 'router bgp 65000\n bgp router-id 10.0.0.1\n' > "$FAKE/frr_running"
  printf 'RPKI Cache Servers\n' > "$FAKE/rpki"
  # The saved configuration, and a dataplane that took all of it: the pool,
  # the export, the MTU, the address and uRPF. Every one of those comparisons
  # has a fixture above where the two sides disagree; this is the box where
  # they agree, which none of them had.
  export HEXA_CONFIG="$FAKE/hexarouting.conf"
  cat > "$FAKE/hexarouting.conf" <<'CONF'
nat cgnat pool alpha
 inside 100.64.0.0/24
 exit
cgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739
cgnat logging tcp 10.0.0.2 port 4739
cgnat logging enable
interface TenGigabitEthernet3/0/0
 mtu 9000
 ipv4 address 200.150.0.1/24
 urpf strict
 exit
CONF
  # An operator account with a role the box granted it AND the memberships
  # that make the role real, which for the whole life of the product was the
  # one thing no box had (#184).
  printf 'root:x:0:0::/root:/usr/bin/hexash-login\nnoc:x:1000:1000::/home/noc:/usr/bin/hexash-login\n' \
    > "$FAKE/passwd"
  printf 'noc hexa-admin vpp frr frrvty\n' > "$FAKE/groups"
  echo 0 > "$FAKE/api_rc"
}

# ============================================================================
# THE VERDICT A ONE-NIC BOX REACHES, which is the sentence an operator reads
# and the only one most of them will (#378).
#
# Not a code path and not a check: the LAST LINE, the one that survives a
# scrolling console. The bar this change was held to is that a box with one
# card comes out of it no worse than it was, and "no worse" is measured here
# or it is not measured at all.
#
# Read through the holder in tools/selftest-verdict.sh, never spelled: four
# readers of one sentence refused a correct medium twice (#311). VERDICT_RE
# is all this needs - which of the four a box reached is decided by comparing
# boxes, not by matching a sentence.
. "$(cd "$(dirname "$0")/.." && pwd)/tools/selftest-verdict.sh"
verdict_of() {  # the last verdict line the run printed
  printf '%s
' "$OUTPUT" | grep -E "$VERDICT_RE" | tail -1
}

echo "=== the verdict a one-NIC box reaches (#378) ==="
# BOX ONE: it claimed its only card, and the dataplane has it. This is the box
# the change exists to produce, and nothing typed to get there.
healthy
mkdir -p "$FAKE/root/sys/bus/pci/devices/0000:00:09.0"
echo "0x020000" > "$FAKE/root/sys/bus/pci/devices/0000:00:09.0/class"
run
_v=$(verdict_of)
if said_a_failed_verdict /dev/stdin <<EOV
$OUTPUT
EOV
then
  bad "a one-NIC box that claimed its card FAILS its own self-test:$_v"
else
  ok "a box that claimed its only card reaches:$_v"
fi

# BOX TWO: it declined the card, because it could not read a way back. The
# state master is in on EVERY one-NIC box today, reached here for a different
# reason - so the verdict must be the one master reaches and not worse.
healthy
mkdir -p "$FAKE/root/sys/bus/pci/devices/0000:00:09.0"
echo "0x020000" > "$FAKE/root/sys/bus/pci/devices/0000:00:09.0/class"
nicconf_keep() { printf '%s\n' "$1" >> "$FAKE/root/etc/vpp/startup.conf"; }
nicconf_keep 'dpdk {
  no-pci
}'
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
IFACE
run
_v2=$(verdict_of)
if said_a_failed_verdict /dev/stdin <<EOV2
$OUTPUT
EOV2
then
  bad "a one-NIC box that declined its card FAILS its own self-test, which is
worse than the box master ships:$_v2
$(printf '%s\n' "$OUTPUT" | grep '\[FAIL\]')"
else
  ok "a box that declined its only card reaches:$_v2"
fi
# AND IT IS THE SAME VERDICT AS A BOX WITH NO CARD AT ALL, which is the box
# master ships every one-NIC appliance as.
#
# COMPARED, not matched against a needle. A third reader spelling one of the
# four verdict sentences is what refused a correct medium twice (#311), and
# the claim here is not "it says these words" - it is "it is no worse than
# the box this replaces". Two boxes and one comparison says exactly that, and
# it keeps saying it if somebody rewords the sentence.
# A box with NO PCI card at all: no hardware to claim, so nothing claimed.
# Same startup.conf, same empty dataplane, and the only difference from the
# box above is whether the machine has a card in it.
healthy
nicconf_keep 'dpdk {
  no-pci
}'
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
IFACE
run
_v3=$(verdict_of)
if [ "$_v2" = "$_v3" ]; then
  ok "and it is the same verdict a box with no card at all reaches, which is
         what master ships every one-NIC appliance as"
else
  bad "a box that declined its card reaches a different verdict from a box
that has no card, so #378 made the one-NIC box worse:
  declined: $_v2
  no card:  $_v3"
fi

healthy
run
# The control's own control, and it comes first. Every assertion in this
# section is about a box the self-test finds nothing wrong with, so if this
# one is red the rest of them are measuring a different box - which is exactly
# how #297's assertion came to pass for the wrong reason. It names the lines
# that went red, because the answer is always one check and one fixture.
if said '\[FAIL\]'; then
  bad "the healthy box fails a check, so nothing below it is about a healthy box:
$(printf '%s\n' "$OUTPUT" | grep '\[FAIL\]')"
else
  ok "$(printf '%s\n' "$OUTPUT" | grep -c '\[ ok \]') checks pass on it and not one goes red"
fi
# The headline only. A continuation row carries no marker, and the summary
# itself says "See the [warn] above" on the box below.
if said '^  \[warn\] '; then
  bad "the healthy box is warned about something, so it is not the box that
has nothing wrong with it:
$(printf '%s\n' "$OUTPUT" | grep '^  \[warn\] ')"
else
  ok "and nothing on it is warned about either"
fi
# THE LINE THE FIELD READS, which until now had never been executed by a test.
# Anchored whole: "ready" appearing somewhere is not the same statement as the
# summary being that and nothing else, and the two boxes below are exactly the
# cases where the difference matters.
if said '^  HexaRouting ready\.$'; then
  ok "so the last line is 'HexaRouting ready.' and nothing more"
else
  bad "a box with nothing wrong did not say it is ready: $(printf '%s\n' "$OUTPUT" | tail -2 | head -1)"
fi

echo "--- the same box with two warnings on it, and still nothing failed ---"
# The second branch under `fail = 0`, and the one #297 exists for: a box with
# five warnings and a box with none used to end identically, on a console that
# scrolls and is read from the bottom by somebody racking a box.
#
# Two warnings that are ordinary hardware rather than a fault: 3 GB of RAM is
# enough to boot and tight under load, and a CPU without avx2 runs VPP and
# delivers less. Neither is a failure, which is the whole point - if either
# were, this box would take the branch above it and the assertion would be
# about something else.
healthy
printf 'MemTotal:       3145728 kB\n' > "$FAKE/root/proc/meminfo"
printf 'model name\t: Synthetic CPU\nflags\t\t: fpu sse4_2\n' \
  > "$FAKE/root/proc/cpuinfo"
run
if said '\[FAIL\]'; then
  bad "the warning box failed a check, so its summary is not the one under test:
$(printf '%s\n' "$OUTPUT" | grep '\[FAIL\]')"
else
  ok "nothing on it failed"
fi
# Read off the box rather than assumed, so that a warning arriving from
# somewhere else makes this red instead of making the count below wrong.
nwarn=$(printf '%s\n' "$OUTPUT" | grep -c '^  \[warn\] ')
if [ "$nwarn" = 2 ]; then
  ok "and it printed the two warnings it was arranged to have"
else
  bad "it has $nwarn warnings, not the 2 arranged: $(printf '%s\n' "$OUTPUT" | grep '^  \[warn\] ')"
fi
if said '^  HexaRouting ready, with 2 warning\(s\) above\.$'; then
  ok "and the last line says it is ready AND how many warnings are above it"
else
  bad "a box with 2 warnings and no failures said: $(printf '%s\n' "$OUTPUT" | tail -2 | head -1)"
fi

echo "--- and the same box with no NIC to forward on, which is racking day ---"
# The third branch, and the box #297 was measured on: installed, healthy, and
# not yet able to do its job. It is not a failure - nothing is broken and
# nothing needs fixing, there is a step left - and it is not "ready" either,
# in the sense the person reading that line means it.
#
# The whole box is the healthy one with the NIC taken away: startup.conf as
# the tuner writes it when no interface has been named, a dataplane holding
# nothing but local0, and a saved configuration that says nothing about an
# interface, because on this box nobody has configured one yet.
healthy
sed -i 's/^  dev 0000:03:00.0$/  no-pci/' "$FAKE/root/etc/vpp/startup.conf"
cat > "$FAKE/show_interface" <<'IFACE'
              Name               Idx    State  MTU (L3/IP4/IP6/MPLS)     Counter          Count
local0                            0     down          0/0/0/0
IFACE
: > "$FAKE/show_lcp"
rm -f "$FAKE/show_interface_addr"
printf 'nat cgnat pool alpha\n inside 100.64.0.0/24\n exit\ncgnat logging udp 10.0.0.2 src 10.0.0.1 port 4739\ncgnat logging enable\n' \
  > "$FAKE/hexarouting.conf"
run
if said '\[FAIL\]'; then
  bad "the no-NIC box failed a check, which would make it the other summary:
$(printf '%s\n' "$OUTPUT" | grep '\[FAIL\]')"
else
  ok "nothing on it failed: it is installed and healthy, and cannot forward"
fi
# said_across, because this sentence is two say() calls and the needle spans
# the break between them. A needle written against the raw text matches only
# while the message happens to fold where it folds today, which is the trap
# this repository has now hit four times (#279, #285).
if said_across 'HexaRouting is installed and healthy, and has no interface to forward on: it is not forwarding. See the \[warn\] above for the one step left.'; then
  ok "it gets a sentence of its own that says which state it is in"
else
  bad "a box with no interface to forward on said: $(printf '%s\n' "$OUTPUT" | tail -3 | head -2 | tr '\n' ' ')"
fi
# The measurement from #297, which was taken on a real appliance at
# 10.95.200.198 and had nothing in this suite holding it: the box that
# prompted the issue printed "HexaRouting ready." with two warnings above it,
# one of them saying it had no NIC to forward on.
if said 'HexaRouting ready'; then
  bad "a box that cannot forward a packet is still called ready: $(printf '%s\n' "$OUTPUT" | grep 'HexaRouting ready')"
else
  ok "and the word 'ready' is not applied to a box that cannot forward"
fi
# And the green tick six lines above the warning, which is the other half of
# #299 (#299's first half is `show cgnat pool`, on the dataplane side).
#
# This box has a pool - healthy() gives it one and taking the NIC away does
# not remove it - so on the build this was written against it printed
# "[ ok ] 1 CGNAT pool(s) configured" while the same run warned that the box
# had been given no NIC. A reader scanning for green takes a tick as an
# answer, and the answer here is that the pool exists and nothing can reach
# it.
if said '\[ ok \] 1 CGNAT pool\(s\) configured'; then
  bad "a pool on a box that cannot forward is still a green tick: $(printf '%s\n' "$OUTPUT" | grep 'CGNAT pool')"
else
  ok "and the pool on it is not reported as a plain [ ok ]"
fi
if said_across '1 CGNAT pool\(s\) configured, and no interface to forward on: no packet can reach them yet'; then
  ok "it says the pool is there AND that nothing can reach it"
else
  bad "the pool line does not say the pools cannot be reached: $(printf '%s\n' "$OUTPUT" | grep -A1 'CGNAT pool' | tr '\n' ' ')"
fi
# Not a second warning for one fact. The box is already warned about the cause
# three checks up, and a count an operator is meant to act on stops meaning
# anything when one cause contributes to it twice. This box has exactly one
# warning and it is the one about the NIC.
_w=$(printf '%s\n' "$OUTPUT" | grep -c '^  \[warn\] ')
if [ "$_w" -eq 1 ]; then
  ok "and it did not become a second warning about a cause already warned about"
else
  bad "the box has $_w warning(s), not the one about the NIC: $(printf '%s\n' "$OUTPUT" | grep '\[warn\]' | tr '\n' ' ')"
fi

echo "=== and the same box with the IPFIX collector in each of its states ==="
# The check #300 is about, driven for the first time. Every fixture in this
# file ended "TCP transport: disabled", so the collector line was never
# produced, `tcp_dst` was always empty and NEITHER arm of the case had ever
# run - which is how `*connected` matching `disconnected`, tested first,
# survived 105 boxes and reported an unreachable collector as "[ ok ] TCP
# export 192.0.2.5:4739 connected".
#
# Variations of the healthy box rather than fixtures of their own, so that the
# collector's state word is the only thing that differs between the three. A
# box assembled from nothing here would be green or red for reasons this
# section is not about, which is exactly how #297's assertion came to pass.
#
# The three words come from bpa_show_logging_command_fn(): connected,
# connecting when pending_connect is set, disconnected otherwise. There is no
# fourth, so the fourth case below arranges one that does not exist - the
# point being that a word this file does not know must be REPORTED and not
# fall through a case with nothing to match.

echo "--- connected, which is the healthy box and must stay green ---"
healthy
bpa_logging_tcp connected
run
# The half a careless fix breaks. Widening the disconnected pattern until it
# stops matching "connected" is easy to do in a way that also stops the OK arm
# matching anything, and the box would then be warned about a collector that
# is working - which is the mutation this assertion exists to catch.
if said '\[ ok \] TCP export 10.0.0.2:4739 connected'; then
  ok "a connected collector is an [ ok ] line that names it"
else
  bad "a connected collector said: $(printf '%s\n' "$OUTPUT" | grep -i 'TCP collector\|TCP export' || echo '<nothing at all>')"
fi
if said '\[warn\].*TCP collector'; then
  bad "a connected collector is warned about: $(printf '%s\n' "$OUTPUT" | grep '\[warn\].*TCP collector')"
else
  ok "and nothing is warned about it"
fi

echo "--- disconnected, which is the state the whole spill file exists for ---"
healthy
bpa_logging_tcp disconnected
run
# Today's build prints "[ ok ] TCP export 10.0.0.2:4739 connected" here.
if said '\[ ok \] TCP export 10.0.0.2:4739 connected'; then
  bad "a DISCONNECTED collector is reported as connected (#300): $(printf '%s\n' "$OUTPUT" | grep 'TCP export')"
else
  ok "a disconnected collector is not called connected"
fi
if said '\[warn\] the TCP collector 10.0.0.2:4739 is disconnected'; then
  ok "it is a warning, and it names the collector and the state"
else
  bad "a disconnected collector said: $(printf '%s\n' "$OUTPUT" | grep -i 'TCP collector\|TCP export' || echo '<nothing at all>')"
fi
# said_across: this sentence is two say() calls under the headline and the
# needle spans the break between them. A needle written against the raw text
# matches only while the message happens to fold where it folds today, which
# is the trap this repository has now hit five times (#279, #285).
if said_across 'records are spilling to /var/log/hexarouting and will be replayed when it comes back'; then
  ok "and it says where the records are going and that they come back"
else
  bad "the warning does not say what a disconnected collector costs: $(printf '%s\n' "$OUTPUT" | grep -A2 'TCP collector' | tr '\n' ' ')"
fi
# A warning and not a failure, because the records are on disk rather than
# lost. If this went red the box would take the other summary branch and the
# operator would be told the boot failed over a collector that is coming back.
if said '\[FAIL\]'; then
  bad "a disconnected collector failed the box: $(printf '%s\n' "$OUTPUT" | grep '\[FAIL\]')"
else
  ok "and it is a warning, not a failure: nothing on the box is broken"
fi

echo "--- connecting, which is not the same thing and is not always transient ---"
healthy
bpa_logging_tcp connecting
run
if said '\[ ok \] TCP export 10.0.0.2:4739 connected'; then
  bad "a collector still connecting is reported as connected: $(printf '%s\n' "$OUTPUT" | grep 'TCP export')"
else
  ok "a collector still connecting is not called connected"
fi
# Its own words, not the disconnected ones. bpa_tcp_disconnect() does not
# clear pending_connect, so a handshake that times out keeps the state word at
# "connecting" and starts over every 5 s: a collector whose SYN is dropped
# reads as "connecting" for as long as the firewall is there. That is worth
# saying, and it is not the same fault as a connection that was refused.
if said '\[warn\] the TCP collector 10.0.0.2:4739 is still connecting'; then
  ok "it is said out loud, in words of its own"
else
  bad "a connecting collector said: $(printf '%s\n' "$OUTPUT" | grep -i 'TCP collector\|TCP export' || echo '<nothing at all>')"
fi
if said 'is disconnected'; then
  bad "a connecting collector is reported as disconnected, which is a different fault"
else
  ok "and it is not reported as the disconnected box above"
fi
if said_across 'records are spilling to /var/log/hexarouting'; then
  ok "and it still says the records are not leaving the box"
else
  bad "a connecting collector does not say the records are spilling: $(printf '%s\n' "$OUTPUT" | grep -A3 'TCP collector' | tr '\n' ' ')"
fi

echo "--- a state word the dataplane does not have today ---"
# The lesson of #300 rather than a state that exists. The bug hid because a
# case with an arm that never runs reads, from the console, exactly like a
# case with no arm at all: both say nothing. So a fourth word has to produce a
# line, and the sed above it has to let one through to be reported.
# A hyphen in it on purpose. The sed that lifts this line out captured the
# state as [a-z]* until #300, so a word shaped like this one never reached the
# case at all and no arm of it - not even a default arm - could have said so.
# The filter in front of a case is part of the case.
healthy
bpa_logging_tcp half-open 198.51.100.254:65535
run
if said '\[warn\] the TCP collector reports a state this self-test does not know'; then
  ok "an unknown state is reported rather than silently dropped"
else
  bad "the collector reported a state this file does not know and the self-test said: $(printf '%s\n' "$OUTPUT" | grep -i 'TCP collector\|TCP export' || echo '<nothing at all>')"
fi
# said_across: the address and the word are on a folded continuation row, and
# where that row breaks depends on how long they are.
if said_across "198.51.100.254:65535 is 'half-open'"; then
  ok "and it names the collector and quotes the word it does not know"
else
  bad "it did not say which collector or which word: $(printf '%s\n' "$OUTPUT" | grep -A3 'TCP collector' | tr '\n' ' ')"
fi
if said '\[ ok \] TCP export'; then
  bad "an unknown state is reported as a working export: $(printf '%s\n' "$OUTPUT" | grep 'TCP export')"
else
  ok "and it is not called a working export"
fi

# $BIN is on the PATH of every run, so the VTY stub goes away with the rest:
# a fixture left behind is a fixture the next section measures by accident,
# which is #222 in one line.
rm -f "$BIN/hexash" "$FAKE/vppctl_ok" "$FAKE/show_plugins" "$FAKE/show_interface" \
      "$FAKE/show_interface_addr" "$FAKE/show_lcp" "$FAKE/statseg" \
      "$FAKE/bpa_pools" "$FAKE/bpa_urpf" "$FAKE/bpa_config" "$FAKE/bpa_logging" \
      "$FAKE/frr_running" "$FAKE/rpki" "$FAKE/oom_score_adj" "$FAKE/mainpid" \
      "$FAKE/keytimes" "$FAKE/mnt_root" "$FAKE/mnt_records"
printf '\n' > "$FAKE/hexarouting.conf"
unset HEXA_CONFIG
rm -rf "$FAKE/root"

# =========================================================================
# The network this box is managed on (#303)
# =========================================================================
#
# Measured on a 0.72.0 appliance: /etc/network/interfaces.d empty,
# networking.service enabled with nothing to do, the card DOWN with no
# address, and NOTHING on the console about any of it. The operator brought
# the card up by hand at the console; the next reboot undid that. Three times.
#
# Every case below is a state a real box is found in, and none of them can be
# arranged on the machine running these tests - it has its own cards, its own
# addresses and its own default route, and it is not going to lose them to
# make a test pass. So the box's configuration is a fake root and the kernel's
# answer is a stubbed `ip`, and what is asserted is what the self-test SAYS
# about the two together.
mgmt_box() {  # <stanza or ""> <addresses on ens18> <default route>
  touch "$FAKE/mgmt.keep"
  rm -rf "$FAKE/root"
  mkdir -p "$FAKE/root/etc/network/interfaces.d" \
           "$FAKE/root/sys/class/net/ens18"
  if [ -n "$1" ]; then printf '%s\n' "$1" > "$FAKE/root/etc/network/interfaces.d/hexarouting"; fi
  printf '%s' "$2" > "$FAKE/ip_addr_ens18"
  printf '%s' "$3" > "$FAKE/ip_default"
}
ADDR18='2: ens18    inet 10.0.2.15/24 brd 10.0.2.255 scope global ens18
'
DEFRT='default via 10.0.2.2 dev ens18 proto dhcp metric 100
'

echo "=== installed console-only, and on the network anyway (#387) ==="
# THE BOX THE MAINTAINER WAS READING THIS ON. The install answered `none`, so
# the file exists and carries no `iface` line, and the self-test said "this
# box is reached at its console" - while it was being read over ssh, on a card
# added after the install.
#
# It is a warning and not an [info] because of what happens next: since #378 a
# card nothing reserves is claimed by the dataplane at the next boot, and on a
# box installed this way the reserve can never exist.
#
# The `device` link is what makes ens18 a CARD here rather than a pair, and it
# is the whole of the guard being asserted: without it this case would be
# indistinguishable from the one below it.
mgmt_box "# NO MANAGEMENT NETWORK was asked for" "$ADDR18" "$DEFRT"
mkdir -p "$FAKE/root/sys/class/net/ens18/device"
run
said "no management network was asked for, and this box is on one anyway" \
  && ok "a console-only box that is on the network says so" \
  || bad "the box answered over the network and still said the management \
network does not apply to it"
said "Linux holds: ens18" \
  && ok "and names the card Linux still holds" \
  || bad "it warned and did not say which card it is about"
said_across "nothing reserves is one the dataplane claims at the next boot" \
  && ok "and says what happens to that card at the next boot" \
  || bad "it says the box is on the network and not what becomes of the card"
said "iface ens18 inet dhcp" \
  && ok "and gives the stanza that reserves it, naming the card the box has" \
  || bad "the advice does not name a card this box actually has"
said "box is reached at its console" \
  && bad "it still says the box is reached at its console, which is the \
sentence that was false" \
  || ok "and does not say the box is reached at its console"

echo
echo "=== the pair a one-card box is reached on is NOT a card to warn about ==="
# The commonest box there is after #378: the only card went to the dataplane
# and the address lives on the linux_cp pair. A pair has no `device` link, so
# it is not something the dataplane can claim - and warning here would be
# crying wolf on every such box, which is how a warning stops being read.
mgmt_box "# NO MANAGEMENT NETWORK was asked for" "$ADDR18" "$DEFRT"
run
said "no management network was asked for, and this box is on one anyway" \
  && bad "it warned about an interface that is a linux_cp pair, which the \
dataplane already holds and cannot claim again" \
  || ok "a pair carrying the address draws no warning"
said "box is reached at its console" \
  && ok "and the console-only box still says so" \
  || bad "the [info] for a genuinely console-only box is gone"

echo
echo "=== a box installed by a medium that never asked (the defect) ==="
# The file is ABSENT, which is every appliance installed up to and including
# 0.72.0. It is a failed check and not a warning: the box comes back from
# every reboot unreachable, and a warning on a console that scrolls is a line
# the verdict at the bottom takes back.
mgmt_box "" "" ""
run
if said '\[FAIL\] no management network: nothing was ever written for one'; then
  ok "it is a failed check, and the headline says the box has none"
else
  bad "a box with no management network at all was not reported: $(printf '%s\n' "$OUTPUT" | grep -i 'management\|managed' || echo '<nothing about it>')"
fi
if said_across "comes back from every reboot with its card down and no address"; then
  ok "and it says what that costs at the next reboot"
else
  bad "the report does not say what an unconfigured box does at a reboot"
fi
# The fix, typed at the console this is printed on, by somebody who cannot
# reach the box any other way. Without the commands it is an alarm nobody can
# act on from where they are standing.
if said_across "ifup eth0"; then
  ok "and it gives the commands that fix it from this console"
else
  bad "the report says the box is unreachable and not how to make it reachable"
fi
# With this box's OWN card names in it. "eth0" is almost certainly not what
# the machine calls its card, and an operator at a console with no network
# cannot look them up without knowing the command to look them up with.
if said_across "in place of eth0, use one of this box's own cards: ens18"; then
  ok "and it names the cards this box actually has"
else
  bad "the instruction says eth0 and never says what this box calls its own"
fi

echo "=== a box whose operator said 'none', which is not the same box ==="
# The installer writes the file even for this answer, and this is the whole
# reason: without it a chosen absence and a forgotten one are the same box on
# disk, and one of them must be shouted about while the other must not.
mgmt_box "# Generated by hexarouting-install. Do not edit by hand.
# NO MANAGEMENT NETWORK was asked for" "" ""
run
if said '\[FAIL\].*management'; then
  bad "an operator who chose no management network is reported as a failure"
else
  ok "it is not a failed check: the answer was given, not forgotten"
fi
if said_across "no management network was asked for at install time"; then
  ok "and the box says which of the two it is"
else
  bad "nothing distinguishes a chosen absence from a forgotten one"
fi

echo "=== the card it was installed on is not in the box any more ==="
mgmt_box "auto ens19
iface ens19 inet dhcp" "" "$DEFRT"
run
if said '\[FAIL\] management network: this box has no card called ens19'; then
  ok "it is a failed check, and it names the card that is missing"
else
  bad "a management stanza for a card the box does not have was not reported"
fi
if said_across "cards this box has: ens18"; then
  ok "and it lists the cards the box does have, which is the next question"
else
  bad "it says the card is gone without saying what is there: $(printf '%s\n' "$OUTPUT" | grep -A3 'no card called' | tail -2)"
fi

echo "=== configured by DHCP, and no server answered ==="
mgmt_box "auto ens18
iface ens18 inet dhcp" "" ""
run
if said '\[FAIL\] management network: ens18 has no address'; then
  ok "a card with no address is a failed check"
else
  bad "a box configured by DHCP with no lease was not reported"
fi
if said_across "no server answered or the cable is not in"; then
  ok "and the two things that cause it are named"
else
  bad "it does not say why a DHCP card has no address"
fi

echo "=== an address on the wrong subnet: the answer that was wrong ==="
# THE CASE THE ISSUE ASKS ABOUT. ifup refuses a gateway outside the
# interface's own subnet - "Error: Nexthop has invalid gateway" - and carries
# on, so the box comes up with an address, no default route, and is reachable
# from its own subnet and nowhere else. From a floor away that is
# indistinguishable from a box that did not boot, and the console is the only
# place it is visible.
mgmt_box "auto ens18
iface ens18 inet static
    address 10.0.2.15/24
    gateway 192.168.99.1" "$ADDR18" ""
run
if said '\[FAIL\] management network: ens18 has an address and no default route'; then
  ok "an address with no default route is a failed check"
else
  bad "a box that came up on the wrong subnet reported nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'managed on\|management' || echo '<nothing>')"
fi
if said_across "The gateway this box was installed with is 192.168.99.1"; then
  ok "and it names the gateway that did not take"
else
  bad "it does not name the gateway, which is the answer to change"
fi
if said_across "the address and the gateway are on the same subnet"; then
  ok "and says which two answers have to agree"
else
  bad "it reports the symptom without the thing to look at"
fi

echo "=== an address, no default route, and none was asked for ==="
# A management LAN with no way off it is a real arrangement - a jump host on
# the same subnet is exactly that - so this is a warning and not a failure.
# The box IS reachable; it is reachable from one place.
mgmt_box "auto ens18
iface ens18 inet static
    address 10.0.2.15/24" "$ADDR18" ""
run
if said '\[warn\] ens18 is up with no default route'; then
  ok "it is a warning: the box is reachable, from one subnet"
else
  bad "a deliberate gateway-less box was not reported as a warning"
fi
if said '\[FAIL\].*management'; then
  bad "and it was ALSO reported as a failure, which it is not"
else
  ok "and it is not a failed check"
fi

echo "=== a box that came back on the network it was installed on ==="
mgmt_box "auto ens18
iface ens18 inet dhcp" "$ADDR18" "$DEFRT"
run
if said '\[ ok \] managed on ens18 \(dhcp\)'; then
  ok "it passes, and says which card and how it was addressed"
else
  bad "a healthy management network did not pass: $(printf '%s\n' "$OUTPUT" | grep -i 'managed on\|management' || echo '<nothing>')"
fi
# THE ADDRESS IS PRINTED, and this is half of why DHCP can be the default at
# all: an operator who did not choose the address still reads it off the
# console of the box in front of them and ssh's to it.
if said_across "10.0.2.15/24"; then
  ok "and the address is on the console, which is where DHCP becomes usable"
else
  bad "a DHCP box does not print the address it was given, so nobody at the console knows where to reach it"
fi
if said_across "default via 10.0.2.2"; then
  ok "and the default route with it"
else
  bad "the route off the subnet is not printed"
fi

echo "=== and a LIVE medium is not asked the question at all ==="
# The live image has no persisted configuration by definition and is reached
# at the console it is being installed from. A [FAIL] here would fail a
# healthy medium on every boot, which is the shape of the defect the DPDK
# check in this same file carries a scar for.
mgmt_box "" "" ""
mkdir -p "$FAKE/root/run/live/medium"
run
if said '\[FAIL\].*management'; then
  bad "the live medium was failed for having no management network"
else
  ok "a live medium is not failed for having no management network"
fi
rm -f "$FAKE/mgmt.keep"
rm -rf "$FAKE/root/run/live"

echo "=== a box whose binary API is not registered (#119) ==="
# THE CHECK THIS WAS ALL FOR. The medium shipped without the vat2 plugin for
# the life of the project: such a box boots, routes, answers ssh, and fails
# every single pool commit, which is the one thing it exists to do. Until now
# the stub answered a constant, so no box this suite arranges had ever seen
# that branch run (#335).
#
# The API list is not empty here: a box whose vat2 answers nothing is a
# different fault, and a fixture that could not tell "no plugin" from "no
# vat2" would pass the day the check started matching the wrong one.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root"
printf 'show_version\nip_route_add_del\n' > "$FAKE/dump_apis"
run
rm -f "$FAKE/dump_apis"
if said '\[ ok \] binary API registered'; then
  bad "a box with no bpa_pool_add_del in its API list was reported as one \
that can be configured"
else
  ok "it does not read as a box whose pools can be configured"
fi
if said '\[FAIL\] binary API NOT registered'; then
  ok "and it is a failed check, not a warning"
else
  bad "a box that cannot be configured at all was not reported: $(printf '%s\n' "$OUTPUT" | grep -i "binary api" || echo '<nothing about the binary api>')"
fi
if said 'the vat2 plugin is missing from this install'; then
  ok "and it names what is missing, so somebody can fix it"
else
  bad "the refusal does not say which plugin: an operator is told the box \
cannot be configured and not why"
fi

echo "=== and a box carrying a file the build did not ship ==="
# Arranged HERE as well as in test/test_manifest.sh, and deliberately so. What
# the manifest MEANS is that suite's question, against a root a real install.sh
# wrote. What this one needs is simply for the line not to be the same on every
# box, because a reader below refuses a check no box has seen fail - and the
# honest way to satisfy a rule is to make the data right, not to write an
# exception into the rule.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/usr/share/hexarouting"
echo "a file of the build this box says it is running" \
  > "$FAKE/root/usr/share/hexarouting/a-file-of-that-build"
( cd "$FAKE/root" && sha256sum usr/share/hexarouting/a-file-of-that-build ) \
  > "$FAKE/root/usr/share/hexarouting/MANIFEST"
echo "and something else wrote to it" \
  >> "$FAKE/root/usr/share/hexarouting/a-file-of-that-build"
run
if said '\[ ok \] all .* files of that build are the ones it shipped'; then
  bad "a box carrying a file the build did not ship was reported as shipped"
else
  ok "a changed file is not reported as the one that shipped"
fi

echo "=== a box whose VTY is installed and dies at start ==="
# 0.2.0 shipped a hexash that died for want of config.py, and that is the
# defect this check was written for. Every box above that had a VTY at all was
# answered by a stub printing one constant line, so the arm that reports a VTY
# which does NOT start had never run - and the fall-through arm it would have
# gone to prints "[ ok ] VTY starts: Traceback (most recent call last):",
# which is a green tick on a box nobody can configure.
#
# Three lines and not one, because what reaches the console is the LAST line:
# a traceback whose last line is the module it could not find is the only form
# of this an operator can act on.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root"
cat > "$BIN/hexash" <<'EOF'
#!/bin/sh
echo "Traceback (most recent call last):"
echo "  File \"/usr/bin/hexash\", line 12, in <module>"
echo "ModuleNotFoundError: No module named 'config'"
exit 1
EOF
chmod +x "$BIN/hexash"
run
rm -f "$BIN/hexash"
if said '\[ ok \] VTY starts'; then
  bad "a VTY that dies at start was reported as one that starts, which is the \
box 0.2.0 shipped"
else
  ok "a VTY that dies at start does not read as one that started"
fi
if said '\[FAIL\] hexash does not start'; then
  ok "and it is a failed check, not a warning"
else
  bad "a box whose VTY cannot start was not reported: $(printf '%s\n' "$OUTPUT" | grep -iE 'hexash|VTY' || echo '<nothing about the VTY>')"
fi
if said "No module named 'config'"; then
  ok "and it carries the last line, which is the reason somebody can act on"
else
  bad "the failure does not say why the VTY died: an operator is told the \
front door is shut and not what is holding it"
fi

echo "=== a box that booted before its tuning did (FIRST BOOT mode) ==="
# The first boot after an install runs with no pinning and no hugepages: the
# kernel command line the tuner wrote does not apply until the reboot after
# it. Such a box forwards at a fraction of what it will, and the one line
# saying so had never been printed by anything here - so the branch beside it,
# "full tuning active", was the only one any box had seen.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/etc/vpp"
printf '# GENERATED by hexarouting-tune\n# FIRST BOOT MODE\nunix {\n}\n' \
  > "$FAKE/root/etc/vpp/startup.conf"
run
if said '\[ ok \] full tuning active'; then
  bad "a box running unpinned with no hugepages was reported as fully tuned"
else
  ok "a first boot does not read as a box whose tuning is in effect"
fi
if said '\[warn\] running in FIRST BOOT mode'; then
  ok "and it says which mode it is in"
else
  bad "a first boot said nothing about being one: $(printf '%s\n' "$OUTPUT" | grep -iE 'tuning|FIRST BOOT' || echo '<nothing about the tuning>')"
fi
if said_across 'reboot for isolcpus and hugepages to take effect'; then
  ok "and what to do about it, which is the whole of the operator's next step"
else
  bad "it named the mode and not the way out of it"
fi

echo "=== a box whose records name an array the kernel has no record of ==="
# The mirror the year of connection records lives on (#83). fstab mounts it
# nofail, so a box whose array never assembled comes up forwarding with the
# volume missing - and every arm of the mirror check but this one is reading a
# /proc/mdstat block that exists. This is the arm for a source that names an
# array and a kernel that does not have it, and nothing had ever run it.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/var/log/hexarouting" "$FAKE/root/proc"
echo /dev/vda2 > "$FAKE/mnt_root"
echo /dev/md9  > "$FAKE/mnt_records"
cat > "$FAKE/root/proc/mdstat" <<'MD'
Personalities : [raid1]
md0 : active raid1 vdb1[1] vda3[0]
      2094080 blocks super 1.2 [2/2] [UU]

unused devices: <none>
MD
run
rm -f "$FAKE/mnt_root" "$FAKE/mnt_records"
if said 'records mirrored on'; then
  bad "a box whose array is not in /proc/mdstat was reported as mirrored, off \
a block belonging to a different array"
else
  ok "it does not count disks out of an array that is not there"
fi
if said '\[FAIL\] the records are mounted from /dev/md9 and the kernel has no such array'; then
  ok "and it names the array it cannot find"
else
  bad "a records volume the kernel has no array for was not reported: $(printf '%s\n' "$OUTPUT" | grep -iE 'record|mirror|md9' | tr '\n' ' ')"
fi

echo "=== the three boxes whose manifest cannot answer for itself (#302) ==="
# Arranged HERE as well as in test/test_manifest.sh, for the reason the
# changed-file box above is: what the manifest MEANS is that suite's question,
# against a root a real install.sh wrote. What this one needs is for every
# branch the self-test can take about it to have a box, because the reader at
# the bottom of this file refuses a message no box has ever printed - and the
# honest way to satisfy a rule is to make the data right, not to write an
# exception into the rule (#335).
#
# All three are WARNINGS and not failures, and that distinction is the point:
# a check that could not look has not agreed with anything, and a box that
# reads as checked when nothing checked it is the quiet half of #302.

# A box with no manifest at all, which is every box installed before #302.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root"
: > "$FAKE/manifest.keep"
run
if said '\[warn\] no /usr/share/hexarouting/MANIFEST'; then
  ok "a box with no manifest says so rather than saying nothing"
else
  bad "a box with no manifest was silent about it: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build' | tr '\n' ' ')"
fi
if said 'files of that build are the ones it shipped'; then
  bad "a box with no manifest reported its files as the ones that build shipped"
fi

# A manifest that is nothing but comments. `grep -c` prints its count AND
# exits 1 when the count is zero, so this used to fall through to the
# comparison and the box read as failing a check nothing had run.
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root/usr/share/hexarouting"
printf '# yours: etc/hexarouting/tune.env\n' \
  > "$FAKE/root/usr/share/hexarouting/MANIFEST"
run
if said '\[warn\] /usr/share/hexarouting/MANIFEST names no files'; then
  ok "a manifest of nothing but comments is reported as checking nothing"
else
  bad "a manifest naming no files did not say so: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build' | tr '\n' ' ')"
fi

# A box whose checker says no in words nothing here recognises. The verdict
# comes off the EXIT STATUS, which is defined behaviour; the text only decides
# how much detail the console gets. A box that cannot be read must still be
# reported, or a broken box becomes a silent one.
rm -f "$FAKE/manifest.keep"
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root"
printf '#!/bin/sh\necho "everything here is wrong"\nexit 1\n' > "$BIN/sha256sum"
chmod +x "$BIN/sha256sum"
run
rm -f "$BIN/sha256sum"
if said '\[FAIL\] this box does not match its own manifest'; then
  ok "a checker that failed in an unfamiliar wording still reports the box"
else
  bad "a box that does not match its manifest read as healthy because the \
checker phrased its refusal differently: $(printf '%s\n' "$OUTPUT" | grep -iE 'manifest|that build' | tr '\n' ' ')"
fi

# And the box that has nothing to check WITH. Defensive and driven rather than
# argued: coreutils is on every box this product runs on, and an assertion
# nothing can make red is an assertion nobody should believe. The PATH is a
# farm of symlinks to the real commands with that one taken out, so the box is
# ordinary in every other respect.
echo "=== and a box with no sha256sum to check the manifest with ==="
FARM=$FAKE/no-sha256sum
rm -rf "$FARM"
cp -as /usr/bin "$FARM" 2>/dev/null || true
rm -f "$FARM/sha256sum"
rm -rf "$FAKE/root"; mkdir -p "$FAKE/root"
if [ ! -x "$FARM/grep" ]; then
  bad "the PATH farm could not be built, so the box with no sha256sum was \
never arranged and the branch that reports it is still unmeasured"
else
  RUN_PATH=$FARM
  run
  RUN_PATH=
  if said '\[warn\] no sha256sum here'; then
    ok "it says nothing checked, rather than passing a box it did not look at"
  else
    bad "a box with no sha256sum did not say so: $(printf '%s\n' "$OUTPUT" | grep -iE 'sha256|manifest|that build' | tr '\n' ' ')"
  fi
  if said 'files of that build are the ones it shipped'; then
    bad "a box with nothing to check with reported its files as checked"
  fi
fi
rm -rf "$FARM"
rm -f "$FAKE/manifest.keep"

echo "=== the exit status, over every box the cases above arranged ==="
# The rule this holds: docs/appliance.md, "always exits zero: the diagnostic
# MUST NOT bring down the boot". It is a diagnostic, and a diagnostic reports
# rather than decides. Several of its ~141 checks go red on a healthy box in an
# unusual but supported configuration, so a status that carried the verdict
# would stop a customer's box in a rack because a check disagreed with it - at
# the one moment its console is what somebody needs.
#
# A control first: an exit status of zero out of boxes that all passed would
# assert nothing at all, so the cases have to have produced [FAIL] lines.
if [ "$runs_with_fail" -gt 0 ]; then
  ok "$runs_with_fail of $runs runs arranged a box with failed checks"
else
  bad "no run produced a [FAIL] line, so the exit status below proves nothing"
fi
# And the control the other way up, which this line read "105 of 105" on until
# #298. A suite in which EVERY box fails something has never executed a single
# thing that only happens when nothing is wrong - the summary an operator reads
# on a working appliance first among them - and it cannot tell an assertion
# that is true of the product from one that is true of every fixture it has.
# That is not a hypothetical: #297's assertion passed for exactly that reason.
if [ "$runs_with_fail" -lt "$runs" ]; then
  ok "and $((runs - runs_with_fail)) of them arranged a box with nothing wrong"
else
  bad "every one of the $runs boxes fails something, so nothing here tests a box that passes (#298)"
fi
if [ "$bad_exits" -eq 0 ]; then
  ok "the self-test exited 0 on all $runs of them"
else
  bad "the self-test answered for the box: $bad_exits of $runs runs exited non-zero ($first_bad_exit)"
fi

echo "=== and what systemd is told to do with that status ==="
# The other half, and without it the assertions above are half the rule. The
# status only matters because a unit carries it: measured on systemd 252 with
# transient units of this exact shape, ExecStart without a "-" turns exit 3
# into Result=exit-code/ActiveState=failed and SIGSEGV into
# Result=signal/ActiveState=failed, while a consumer carrying Requires= on a
# failed unit never starts at all ("A dependency job for ... failed"). The
# console getty is such a consumer (#93). So a wrong Type=, a missing "-" or a
# Requires= somewhere can make a non-zero status matter even while this script
# exits 0, and can make exiting 0 not help.
UNIT="$REPO/appliance/systemd/hexarouting-selftest.service"
EXECSTART=$(grep -E '^ExecStart=' "$UNIT")
# Anchored on ExecStart= so that the comment block explaining the "-" cannot
# satisfy this on its own.
if [ "$EXECSTART" = "ExecStart=-/usr/lib/hexarouting/selftest" ]; then
  ok "ExecStart= ignores the status: $EXECSTART"
else
  bad "ExecStart= makes the diagnostic's status the unit's verdict: $EXECSTART"
fi
# oneshot with RemainAfterExit is what makes "the self-test has run" an
# orderable fact. Type=notify or Type=forking would fail the unit on a script
# that exits 0 without doing what that type promises, so exiting 0 would stop
# helping.
if grep -qx 'Type=oneshot' "$UNIT" && grep -qx 'RemainAfterExit=yes' "$UNIT"; then
  ok "Type=oneshot with RemainAfterExit=yes, so exiting 0 is enough and is remembered"
else
  bad "the unit is not oneshot/RemainAfterExit: $(grep -E '^(Type|RemainAfterExit)=' "$UNIT" | tr '\n' ' ')"
fi
# Restart= on a diagnostic would put the report back on the console in a loop,
# on top of whatever the operator is reading.
if grep -qE '^Restart=' "$UNIT"; then
  bad "the unit restarts the diagnostic: $(grep -E '^Restart=' "$UNIT")"
else
  ok "no Restart=, so the report is printed once"
fi
# And nothing may make its state fatal to anything else. The getty drop-in is
# written by build-iso.sh rather than shipped as a file, so both are read.
HARD=$(grep -rnE '^(Requires|Requisite|BindsTo|PartOf)=.*hexarouting-selftest' \
         "$REPO/appliance/" || true)
if [ -z "$HARD" ]; then
  ok "nothing Requires=/BindsTo= the self-test, so its state cannot fail another unit"
else
  bad "a hard dependency on the self-test can carry its failure onward: $HARD"
fi

echo "=== no warning added later can escape the count (#297, #298) ==="
# WHAT IS LEFT OF READING THE SOURCE, and what is left is the one question no
# box can answer.
#
# Four assertions stood here, all of them reading appliance/selftest as text,
# and #297 recorded that as a retreat rather than hiding it: the summary has
# four branches, three of them under `fail = 0`, and not one of the 105 boxes
# this suite arranged reached that. Driving them through a box was tried first
# and the assertion passed for the wrong reason - the no-pci box carries eight
# failures of its own, so "does not say ready" was true whatever the summary
# did, and both mutations of it stayed green.
#
# #298 built the box that can answer them. Three of the four are now
# measurements on real boxes, three sections up - "HexaRouting ready.", "ready,
# with N warning(s) above." and the sentence a box that cannot forward gets -
# and each of those was shown red under a mutation of the summary that
# produces it. They are gone from here rather than kept alongside, because two
# checks of one fact disagree eventually and the weaker one is the one people
# then believe.
#
# THIS ONE STAYS, because it is not about what the summary says on any
# particular box. It is about a warning added later with a bare `say`, which
# is invisible to the count on EVERY box: the box still prints it, the
# operator still reads it if they are looking, and the count it is missing
# from is the thing that tells them to look. A measurement would have to know
# how many warnings this file is supposed to have, which is a number that
# changes every time somebody adds one - and the box above would go on saying
# "ready, with 2 warning(s) above" while a third went uncounted.
#
# warn()'s own definition is the one line that legitimately spells it out, so
# it is excluded by name rather than by a looser pattern that would also let a
# real bypass through. Found by this check on its first run, which is the
# argument for writing the exclusion narrowly.
stray=$(grep 'say "  \[warn\]' "$ST" | grep -cv '^warn() {' || true)
if [ "${stray:-0}" -eq 0 ]; then
  ok "every [warn] headline goes through warn(), so none can escape the count"
else
  bad "$stray [warn] headline(s) bypass warn() and are not counted"
fi

echo "=== the verdict the last line prints, and the lab that reads it ==="
# A CONTRACT BETWEEN TWO FILES, and it cost a refused release before anything
# compared them.
#
# lab/install-iso.sh boots an installed box and asserts that the self-test
# reported itself before the getty offered a login. It anchored that on the
# literal "HexaRouting ready." - correct when there was one verdict, and #297
# gave the summary three. A box with no NIC to forward on then reported itself
# correctly, in the right order, and the release gate refused 0.72.0 over it.
#
# The lab is fixed - it anchors the ORDER on the closing row, which is printed
# on every path, and asks separately WHICH verdict was given - and this is what
# stops the two drifting again. Every sentence the self-test can end with has
# to be one the lab knows how to read.
#
# FOUR of them, and the fourth was missing from this list until a mutation
# removed its branch and nothing went red. "HexaRouting ready, with N
# warning(s) above" is NOT matched by a needle for "HexaRouting ready." - the
# comma is exactly where they part - so the lab knew three of the four and
# would have failed a healthy box that happened to carry a warning.
#
# By the distinctive half of each sentence, not the whole line: two of the
# four interpolate a count, and the lab matches a prefix for the same reason.
ST_SRC=$(cat "$ST")
# The lab's own pattern, read out of the lab, and every sentence the self-test
# can print tested AGAINST it rather than looked for inside it. One pattern
# legitimately covers two sentences - "HexaRouting ready" matches both the
# plain verdict and the one with a warning count - so a check that demanded
# the literal string would report a correct lab as broken. That is the shape
# this whole run of failures is about, and it happened here too, on the first
# version of this check.
LAB_RE=$(sed -n 's/^VERDICT_RE="\(.*\)"$/\1/p' "$REPO/tools/selftest-verdict.sh")
verdicts_found=0
if [ -z "$LAB_RE" ]; then
  bad "tools/selftest-verdict.sh has no VERDICT_RE, so nothing reads a verdict by a rule this can check"
fi
# The sentences as the self-test prints them, with the counts filled in: the
# pattern has to match what reaches a console, not what the source spells.
for _v in "  HexaRouting ready." \
          "  HexaRouting ready, with 2 warning(s) above." \
          "  HexaRouting is installed and healthy, and has no interface to forward on:" \
          "  3 check(s) failed. The box is NOT ready."; do
  case "$ST_SRC" in
    *"$(printf '%s' "$_v" | sed 's/^  //; s/2 warning(s)/$warns warning(s)/; s/3 check(s)/$fail check(s)/; s/no interface to forward on:/$noforward:/')"*) ;;
    *) bad "appliance/selftest can no longer print '$_v', so the lab reads for a verdict that does not exist"
       continue ;;
  esac
  verdicts_found=$((verdicts_found + 1))
  if [ -n "$LAB_RE" ] && ! printf '%s\n' "$_v" | grep -qE "$LAB_RE"; then
    bad "the self-test can end with '$_v' and VERDICT_RE does not match it: an installed box printing it is a box the gate reads as never having booted"
  fi
done
if [ "$verdicts_found" -eq 4 ]; then
  ok "all four verdicts the self-test can end with match VERDICT_RE"
else
  bad "only $verdicts_found of the four verdicts were found in appliance/selftest, so this check is asserting almost nothing"
fi

# AND IN ONE PLACE IN THAT FILE, which is the half the first version of this
# check could not see.
#
# It asked whether the lab knew each sentence SOMEWHERE, and the lab knew them
# all - in the ordering assertion, which had just been fixed. Four hundred
# lines below it a second reader went on grepping for "HexaRouting ready" to
# decide whether the installed box had come up at all, so a box that printed a
# complete report was reported as a box that never booted, and the release gate
# refused the medium a second time for the first time's reason.
#
# So the strings live in VERDICT_RE and nothing else in that file may spell
# them outside a comment. A third reader written by hand goes red here rather
# than at a release.
# EVERY file that reads a console, not just the one that was fixed first.
#
# After both fixes there were still two readers spelling the old needle by
# hand - lab/boot-iso.sh and tools/release-gate.sh - and they went on passing
# for a reason that is luck rather than design: a LIVE medium takes the branch
# where DPDK is off deliberately, so it ends with "HexaRouting ready." and the
# old needle matched. A live image that ever ended any other way would have
# had the gate refuse the release and name the wrong cause.
#
# The gate is the file where a false red is most expensive, so it is in this
# list first and not last.
# DERIVED FROM VERDICT_RE, not typed out here. The hand-written list covered
# the two sentences that mean the box is fit to hand over and not the one that
# means it failed - so lab/install-iso.sh spelled "check(s) failed. The box is
# NOT ready." by hand in two places and this check said nothing, for as long as
# it has existed. A rule about second copies that itself keeps a second copy of
# what to look for is the same defect wearing the check's clothes (#311).
#
# The pattern is a grep -E alternation, so the alternatives are its `|` parts
# with the regex escapes taken back out.
# THE NEEDLE IS THE LAST SENTENCE OF EACH ALTERNATIVE, minus its full stop.
# A reader that spells the whole of "check(s) failed. The box is NOT ready."
# is caught by the whole string, and one that spells only "The box is NOT
# ready" is not - which is exactly what lab/install-iso.sh had. Taking the
# final sentence catches both, and dropping the stop catches a reader that
# left it off.
#
# WHAT THIS STILL MISSES, said out loud rather than left to be discovered: a
# reader spelling a shorter fragment than the final sentence. "NOT ready"
# alone would pass. There is no sound way to derive "distinctive enough" from
# the pattern, and a list of fragments typed here would be the second copy
# this check exists to forbid.
SPELL=$(printf '%s' "$LAB_RE" | sed 's/\\(/(/g; s/\\)/)/g; s/\\\././g' \
        | tr '|' '\n' \
        | sed 's/^.*\. //; s/\.$//' )
spelled=0
for _f in tools/release-gate.sh lab/boot-iso.sh lab/install-iso.sh; do
  _hits=$(printf '%s\n' "$SPELL" | while IFS= read -r _s; do
            [ -n "$_s" ] || continue
            grep -nF -e "$_s" "$REPO/$_f" 2>/dev/null
          done | grep -vE "^[0-9]+:[[:space:]]*#" | sort -u)
  _n=$(printf '%s' "$_hits" | grep -c . || true)
  if [ "$_n" -ne 0 ]; then
    spelled=$((spelled + _n))
    bad "$_n line(s) of $_f spell a verdict instead of sourcing tools/selftest-verdict.sh: a second reader of the same truth refused 0.72.0 twice"
    printf '%s\n' "$_hits" | sed 's/^/        /'
  fi
done
if [ "$spelled" -eq 0 ]; then
  ok "and no reader spells them: all three source tools/selftest-verdict.sh"
fi

# EVERY READER THE HOLDER DEFINES HAS A CALLER.
#
# said_a_good_verdict() sat in tools/selftest-verdict.sh for two releases with
# nothing calling it, and the consequence was not cosmetic: every reader asked
# said_a_verdict instead, which is true of "N check(s) failed. The box is NOT
# ready.", so tools/release-gate.sh accepted a medium that had just said it was
# not fit to hand over and reprinted that line under its own "is ready to
# publish" (#311).
#
# A function nobody calls is the quietest way for a rule to stop applying: the
# holder still reads as though the rule is enforced, and the file that would
# have enforced it is the one that stopped. Derived from the holder rather than
# listed, so a fifth reader added later has to be called too.
uncalled=""
for _fn in $(grep -oE "^said_a_[a-z_]+\(\)" "$REPO/tools/selftest-verdict.sh" \
             | tr -d "()"); do
  # A CALL, not a mention. The comment above the gate's call names the
  # function while explaining why it had none, and a grep that counted that
  # would have been satisfied by the very sentence describing the defect.
  _callers=$(grep -hn "$_fn" "$REPO/tools/release-gate.sh" \
                            "$REPO/lab/boot-iso.sh" \
                            "$REPO/lab/install-iso.sh" 2>/dev/null \
             | grep -vE "^[0-9]+:[[:space:]]*#" | grep -c . || true)
  [ "$_callers" -eq 0 ] && uncalled="$uncalled $_fn"
done
if [ -n "$uncalled" ]; then
  bad "tools/selftest-verdict.sh defines$uncalled and no lab or gate calls it: a reader nobody calls is a rule that has quietly stopped applying, and the holder still reads as though it had not"
else
  ok "every verdict reader the holder defines has a caller"
fi

echo "=== every self-test line fits the 80-column console it is printed on ==="
# The self-test writes to /dev/console on a racked appliance, which is 80
# columns. Nothing measured that. A line goes over only when a value is
# interpolated into it, so reading the source finds nothing and driving every
# box does.
#
# EVERY LINE, not only the tuning ones. This was scoped to the lines #163 was
# answerable for, with the other six recorded in a comment rather than fixed.
# They are fixed now (#285): the widest was the worker-pinning failure at 153
# columns on an 80-column console, which is the message an operator reads at a
# serial console because the box is forwarding badly.
#
# A line only goes over when a value is interpolated into it, so reading the
# source finds nothing and driving every fixture finds them.
LAT=$FAKE/latency-lines
cp "$FAKE/every-line" "$LAT"
if [ ! -s "$LAT" ]; then
  bad "no self-test output was collected at all, so this check measured" \
      "nothing: an empty file has no line over 79 columns"
else
  WIDE=$(awk 'length > 79 {print length": "$0}' "$LAT" | sort -u)
  if [ -z "$WIDE" ]; then
    ok "$(sort -u "$LAT" | wc -l | tr -d ' ') distinct lines over $runs boxes, widest $(awk '{print length}' "$LAT" | sort -rn | head -1) columns"
  else
    bad "lines wider than the 79-column console:
$WIDE"
  fi
fi

echo "=== no check may have gone green on every box this suite arranges ==="
# THE CLASS, not the instance. An [ ok ] line that appears on EVERY box here is
# a check whose failure nobody has ever seen: whatever it reads answered the
# same way every time, so the branch that reports a broken box has never run.
#
# It has happened three times in this file, and the first two were each found
# and repaired alone. The systemctl stub carries both in its comments - it was
# `echo 0` for NRestarts, so "it is looping" was unreachable, and the constant
# `inactive` for is-failed, so the branch reporting the CGNAT configuration as
# not applied had never run (#222). The third was `echo bpa_pool_add_del` in
# the vat2 stub, which made "binary API NOT registered" unreachable - the check
# written for #119, on a box that routes and answers and cannot be configured
# at all (#335). Repairing them one at a time is what produced the third.
#
# MEASURED FROM THE RUNS, not from the source. A stub that answers a constant
# looks like any other stub; what gives it away is a hundred and twenty boxes
# all being told the same thing. Duplicated lines within one run are collapsed
# first, so a line printed twice on one box does not read as two boxes.
#
# The rule has no exceptions in it, deliberately. When it went red on the
# manifest line - whose red form IS measured, next door in
# test/test_manifest.sh - the repair was to arrange that box here too, so the
# line stops being universal. An excused line is one nobody re-examines, and
# this whole check exists because three of those accumulated.
if [ ! -s "$FAKE/per-box-lines" ]; then
  bad "no box's lines were kept, so this asked nothing"
else
  _always=$(sort "$FAKE/per-box-lines" | uniq -c \
            | awk -v n="$runs" '$1 == n' | sed 's/^ *[0-9]*  *//' \
            | grep '^\[ ok \]' || true)
  if [ -n "$_always" ]; then
    bad "these check(s) went green on all $runs boxes, so nothing here has \
ever seen them fail - which is how a stub that answers a constant hides:"
    printf '%s\n' "$_always" | sed 's/^/    /'
  else
    ok "$runs boxes, and every [ ok ] line has a box that does not print it"
  fi
fi

echo
echo "=== and no check may have gone unseen: every message has a box ==="
# THE OTHER HALF OF THE READER ABOVE, and the half that would have caught the
# three repairs before they were made. That one refuses an [ ok ] line printed
# by EVERY box, which is a check whose red form nothing has seen. This one
# refuses a message printed by NO box, which is a whole branch nothing has
# run - and the two are not the same thing. The vat2 constant showed up as
# both; the hexash stub showed up only as this, because the line it hid
# behind was printed by a handful of boxes and not by all of them.
#
# Swept by hand once, over 126 boxes, and seven branches of appliance/selftest
# had never been executed by anything in the repository: a VTY that is
# installed and dies at start (the defect of 0.2.0), a box running in FIRST
# BOOT mode, a records volume naming an array the kernel has no record of, and
# four ways a manifest can fail to answer for itself (#302). Each is now a box
# above. This is here so the eighth does not need somebody to think of looking.
#
# READ FROM THE PRODUCT'S SOURCE, against the runs. There is no list here to
# keep up to date: the call sites are found in appliance/selftest itself, so a
# message added there without a box added here goes red on the next run. That
# is the same bargain the healthy() box makes, one level up.
#
# THE NEEDLE IS THE LONGEST RUN OF THE MESSAGE THAT CARRIES NO VALUE. A
# message is mostly literal text with values interpolated into it, and the
# values differ from box to box - so every $var, ${var}, $(...) and `...` is
# cut out first and the longest surviving run is what is looked for. Cutting
# them out rather than globbing over them matters: "$nbad file(s) ..." would
# otherwise be hunted for under the variable's NAME, which no box ever prints,
# and the rule would go red on correct work. A check that goes red on correct
# work is one people learn to run around, which is #333.
#
# NO EXCEPTIONS IN THE RULE, deliberately, and for the third time in this
# file: four of the seven are also measured next door in test/test_manifest.sh
# against a root a real install.sh wrote, and the repair was still to arrange
# them here rather than to excuse them. An excused line is one nobody
# re-examines, and this whole pair of readers exists because those accumulate.
if [ ! -s "$FAKE/every-line" ]; then
  bad "no box's output was kept, so this asked nothing"
else
  _sites=0
  _unseen=""
  _unreadable=""
  # <source line><tab><needle>, one row per ok/bad/warn call site, including
  # the ones that are the body of a `case` arm rather than a line of their own.
  awk '
    {
      s = $0
      if (!sub(/^[ \t]*(ok|bad|warn)[ \t]+"/, "", s)) {
        if (!sub(/^.*[);|&][ \t]*(ok|bad|warn)[ \t]+"/, "", s)) next
      }
      gsub(/\$\([^)]*\)/, "\001", s)
      gsub(/`[^`]*`/,     "\001", s)
      gsub(/\$\{[^}]*\}/, "\001", s)
      gsub(/\$[A-Za-z_][A-Za-z0-9_]*/, "\001", s)
      gsub(/\$[0-9@*?#]/, "\001", s)
      # THE CLOSING QUOTE ENDS THE MESSAGE, and only here. Before the values
      # are punched out the first quote on the line can be one inside a $(...),
      # and cutting there truncates the message and leaves shell in it; after
      # them, the first quote left is the closing one. A `case` arm is
      # `ok "..." ;;`, and without this cut the trailing `;;` becomes a
      # fragment the reader then demands of a printed line - it accused four
      # branches that run on every box.
      gsub(/\\./, "\001", s)
      sub(/".*$/, "", s)
      # EVERY literal fragment, in order, not just the longest one. Two call
      # sites can share their longest chunk and differ only in what follows:
      # measured here, "statistics segment " is the longest chunk of both the
      # 75%-full refusal and the healthy report, and "records mirrored on "
      # of both mirror lines. A reader keyed on the longest chunk alone cannot
      # tell those apart, so it either misses the branch that never ran or
      # accuses the one that did.
      out = ""; n = split(s, part, "\001")
      for (i = 1; i <= n; i++) if (length(part[i]) > 0)
        out = (out == "" ? part[i] : out "\001" part[i])
      printf "%d\t%s\n", FNR, out
    }' "$ST" > "$FAKE/messages"
  # EACH LINE IS SPENT ON THE MESSAGE IT REALLY CAME FROM, which is the
  # longest needle that line contains. A plain "does any line contain this"
  # lets a short message be satisfied by a longer one printed somewhere else:
  # measured on this tree, 7 of the 113 needles are substrings of another, and
  # 2 of those were being reported as covered by lines belonging to their
  # longer sibling - the statistics segment at 75% full, which is what warns
  # an operator before VPP aborts on it, and a records mirror that is
  # synchronising. Both had never been printed by any box.
  #
  # Assigning each line to its most specific message is the whole fix, and it
  # is one awk pass rather than a rule with exceptions in it.
  awk -F'\t' '
    function literal(frags,   i, m, tot) {
      tot = 0; m = split(frags, f, "\001")
      for (i = 1; i <= m; i++) tot += length(f[i])
      return tot
    }
    # In ORDER, and all of them. A line that carries the fragments of the
    # more specific message carries the less specific one s as well, so the
    # order walk is what separates them and the total literal length is what
    # decides which one the line is spent on.
    function ordered(line, frags,   i, m, at, p) {
      m = split(frags, f, "\001"); at = 1
      for (i = 1; i <= m; i++) {
        p = index(substr(line, at), f[i])
        if (p == 0) return 0
        at = at + p + length(f[i]) - 1
      }
      return 1
    }
    NR == FNR { if (literal($2) >= 8) need[$1] = $2 ; next }
    {
      bestlen = 0; best = ""
      for (ln in need)
        if (ordered($0, need[ln]) && literal(need[ln]) > bestlen) {
          bestlen = literal(need[ln]); best = ln
        }
      if (best != "") seen[best] = 1
    }
    END { for (ln in seen) print ln }
  ' "$FAKE/messages" "$FAKE/every-line" | sort -n > "$FAKE/messages-seen"
  while IFS="$(printf '\t')" read -r _ln _needle; do
    _sites=$((_sites + 1))
    # A message made almost entirely of values cannot be recognised in a run,
    # and saying so is the honest answer: passing it would be this reader
    # reporting a branch as covered because it could not look.
    _lit=$(printf '%s' "$_needle" | tr -d '\001')
    if [ "${#_lit}" -lt 8 ]; then
      _unreadable="$_unreadable
    selftest:$_ln"
    elif ! grep -qx -- "$_ln" "$FAKE/messages-seen"; then
      _unseen="$_unseen
    selftest:$_ln  $_needle"
    fi
  done < "$FAKE/messages"
  if [ -n "$_unseen" ]; then
    bad "appliance/selftest can print these and no box here ever made it do \
so, which is a branch that has never run anywhere - add a box, do not add an \
exception:$_unseen"
  elif [ -n "$_unreadable" ]; then
    bad "these messages are almost all interpolated value, so this reader \
cannot tell whether a box printed them - give the message some words of its \
own:$_unreadable"
  else
    ok "$_sites messages appliance/selftest can print, and a box for every one"
  fi
fi

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
