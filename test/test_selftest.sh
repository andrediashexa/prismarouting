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
# Only the free-space line matters to the check, and only that it is there.
cat > "$BIN/df" <<'EOF'
#!/bin/sh
echo "Filesystem Size Used Avail Use% Mounted"
echo "/dev/vdb1 40G 1G 37G 3% /var/log/hexarouting"
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
  # The root is always pointed at the fake box, even for the cases that put
  # nothing in it: an absent file there reads the same as the absent one on
  # this machine, and a developer box that really does run VPP would otherwise
  # give these tests a different /etc/vpp/startup.conf than the build runner.
  # Same reason systemctl is stubbed whole rather than passed through.
  OUTPUT=$(PATH="$BIN:$PATH" HEXA_SELFTEST_OUT=/dev/stdout \
           HEXA_SELFTEST_ROOT="$FAKE/root" sh "$ST" 2>/dev/null)
  rc=$?
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
if said '\[ ok \] UDP export running to 10.0.0.2:4739'; then
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
rm -f "$FAKE/frr_running"

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
# A box that forwards CGNAT and runs no routing at all is a supported
# configuration of this product. Failing it over a pair it does not need is
# how a self-test's failures stop being read.
if said '\[FAIL\].*pair'; then
  bad "a box with no routing configured was FAILED over a missing pair"
else
  ok "and with no routing configured it is not a failed check"
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
if said '\[FAIL\] startup.conf pins workers to 1 2 3 but the running dataplane holds'; then
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
if said '\[FAIL\] startup.conf pins workers to 1 2 3 and the running dataplane has no'; then
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
if said '\[ ok \] rx queues: *$'; then
  bad "the rx-queues line printed a label and no value at all"
elif said '\[ ok \] rx queues: default'; then
  ok "a box whose profile names no rx queues says so, and says what it means"
else
  bad "nothing reported rx queues: $(printf '%s\n' "$OUTPUT" | grep -i 'rx queue' || echo '<nothing>')"
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
run
if said '\[FAIL\] vpp.service has restarted 7 times: it is looping'; then
  ok "a loop is a FAILED check, and names the count"
else
  bad "a looping vpp.service passed: $(printf '%s\n' "$OUTPUT" | grep -i restart || echo '<nothing>')"
fi
rm -f "$FAKE/nrestarts"

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
if said '\[FAIL\] the dataplane has no interface: 0000:03:00.0 is named'; then
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
if said '\[warn\] no dataplane interface: VPP was given no NIC'; then
  ok "a box deliberately given no NIC is a warning, and says how to give it one"
else
  bad "no-pci said nothing: $(printf '%s\n' "$OUTPUT" | grep -i 'NIC\|dpdk' || echo '<nothing>')"
fi
# THE COMMAND, not a path and an editor. This is the message an operator reads
# on the console of a box that has just come up forwarding nothing, and until
# #283 it sent them out of the VTY to vi - which is the defect, not the
# remedy. The same words the VTY's own diagnosis prints: two accounts of one
# fact that disagree are worse than one.
if said "dataplane interface <name>" && said "commit"; then
  ok "and the way to give it one is a command in the VTY"
else
  bad "the advice does not name the statement that fixes it: $(printf '%s\n' "$OUTPUT" | grep -A4 -i 'no dataplane interface' || echo '<nothing>')"
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
run
if said '\[FAIL\] hexarouting.service FAILED: the CGNAT configuration was not applied'; then
  ok "a failed configuration service is a FAILED check"
else
  bad "hexarouting.service failed and nothing said so: $(printf '%s\n' "$OUTPUT" | grep -i hexarouting.service || echo '<nothing>')"
fi
rm -f "$FAKE/is_failed"
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
if said '\[FAIL\] the saved configuration has address\(es\) the dataplane does not: TenGigabitEthernet0/0/0:203.0.113.1/24'; then
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
if said '\[FAIL\] the saved MTU is not the MTU the dataplane has: TenGigabitEthernet0/0/0\(9000/1500\)'; then
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

if [ "$fail" -eq 0 ]; then echo; echo "ALL TESTS PASSED"; exit 0; fi
echo; echo "$fail FAILURES"; exit 1
