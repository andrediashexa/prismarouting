# The box's record of events: `show log` (#588)

## TL;DR

`show log` is what happened on this box, in Prisma's words, newest last:
commits, rollbacks and `commit confirmed` expiries with who and from where;
restarts, upgrades, reboots and shutdowns asked for, by whom; and each start of
the dataplane and of the routing daemons **with its cause** - the boot, an
operator's `system dataplane restart`, an upgrade, or "unexpected" when
nothing on the box asked for it. It is one file on the log volume, bounded,
kept across reboots, written by the components that act and never scraped
from a journal - so the door calls and timer skips that fill the journal never
reach it. The API reads it with `OperationalService.ListEvents`, and
`GetSystem` carries why the dataplane and the routing daemons last started.

Since phase 2 it is also what the box **sees** happen, recorded by a watcher
on the host that compares each reading of the box's state with the one before:
an interface's link down or up (a port or a sub-interface, administrative or
the wire), a module put in, taken out or past its thresholds, a BGP or BFD
session up or down with FRR's reason or bfdd's diagnostic, a CGNAT pool out of
blocks, the log exporter's collector lost and found, logins and failed logins,
the SNMP and API settings applied or refused, and the dataplane given up on. A
subject that flaps is held and said once a window, with its count. `show log
kind <kind>` selects one kind.

Since phase 3 what already **stands** when the watcher starts - a port up
and dark, a module past its warning, a session not up, a pool out of blocks,
a collector not reached - is said once a boot, in the present tense
("standing at start: ..."). The record **goes** where the operator says:
`system` > `logging` sends it to remote syslog collectors (RFC 5424, UDP or
TCP, a minimum severity, a source address or a VRF), from the same watcher,
with the record itself as the spool. It is **followed** with `monitor log`
and the API's streaming `WatchEvents`, and a pool with no block free and a
module's level moving are SNMP notifications beside the links' and BGP's
standard ones.

Since #588's last phase the watcher also reads the **cards Linux holds** -
the management card, a spare port - for their links and their modules (the
kernel's module EEPROM interface, as `ethtool -m`), and a link that went
**down and back between two readings** is still an event, with how many
times: the dataplane counts each port's link changes (`show bpa
link-changes`) and Linux counts each card's carrier changes.

FRR's `show logging` is unchanged: each routing daemon's logging
*configuration*, as before. Phase 1 is delivered in 0.151.14, phase 2 in
0.152.0, phase 3 in 0.152.2, and the cards Linux holds and the flap between
two readings in 0.152.11 ([the last phase](#the-last-phase-linuxs-cards-and-a-flap-between-two-readings)).

> The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** and **MAY**
> in this document are to be interpreted as described in
> [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

## 5W2H

| | |
|---|---|
| **What** | A record of events on the box: time, severity, source, one sentence, and who and from where when known. `show log` and `ListEvents` read it. |
| **Why** | On 2026-10-05 the operator found the VTY's logs "totally useless": `show logging` printed FRR's per-daemon logging configuration, and the journal on 10.40.200.166 had "door: uid 1000: hostname -> 0" every 2 s and a confirm timer's skip every 16 s. The same day prisma-web said "the dataplane restarted by itself" about an upgrade, because the box recorded no restart cause. |
| **Who** | Written by the code that acts: the VTY and the API (as the account), the host's apply (`prismash --load`, `--reapply`, `--confirm-expired`), the upgrade, `watch-the-dataplane` and `prisma-hot-card` when they give up; and by the host's watcher (`prismash --watch-events`, root) for what the box sees happen. Read by every account. |
| **When** | At the moment of the action; a restart's cause before the restart; what the box sees within one reading of it (10 s; a module's levels and a pool, 60 s and 30 s). |
| **Where** | `/var/log/prisma-routing/events.log` (and `events.log.1`), on the log volume. |
| **How** | One JSON object per line, appended in one write; through the host's door from the management container, locally on the host. |
| **How much** | 256 KiB per file, two files: about 3000 events, years of commits and restarts. |

## The phases

| Phase | What | State |
|---|---|---|
| **1** | The record itself, bounded and kept across reboots. Commits, rollbacks, `commit confirmed` confirmed and expired, with the account and where the session came from. Upgrades (asked, and done). Reboots and shutdowns asked from the VTY or the API. Dataplane and routing starts with their cause, the cause recorded before the action. `show log [last <n>] [since <time>] [severity <level>] [source <name>]`, and the box's `| match`. `ListEvents`, and the cause in `GetSystem`. | *delivered in 0.151.14* |
| **2** | What the box sees happen rather than what it is told: interface and link up/down, administrative or not, ports and sub-interfaces; a module inserted, removed, swapped, and its RX power or temperature past a threshold and back (#590's levels); BGP and BFD sessions up/down with the reason (FRR's last reset, bfdd's diagnostic); a CGNAT pool out of blocks and with blocks again; the log exporter losing or reaching its collector (`logstate`'s readings); logins, failed logins and logouts (wtmp and btmp); SNMP and API settings applied or refused; the dataplane given up on (`watch-the-dataplane`, `prisma-hot-card`). Each from a watcher that compares a reading with the last one, as `observe_starts()` does - never from a log line. Flapping held and counted. `show log kind`; ListEvents' `kind`, `subject`, `reason`. | *delivered in 0.152.0* |
| **3** | What stands when the watcher starts, said once a boot (the X520's RX below its warning on 10.40.200.166, never said). Where the record goes and how it is followed: `system` > `logging`, remote syslog destinations in Prisma's terms (address, port, UDP or TCP, a minimum severity, a source address or a VRF), sent by the watcher from events.log; `show system logging`; `monitor log`, a follow mode with `show log`'s filters, and a streaming `WatchEvents` in the API; SNMP notifications where PRISMA-ROUTING-MIB has an object (#507's machinery): a pool with no block free and a module's level moving, beside the standard linkDown/linkUp and bgpBackwardTransition already sent. | *delivered in 0.152.2*; the BFD notification in 0.152.3 (#559) |
| **4** | What phases 2 and 3 left: the cards Linux holds - their links and their modules, the same events and standing conditions as a dataplane port's, read by the watcher in its own process (no door); and a link that went down and came back between two readings, from counts of the link's changes - the plugin's (`show bpa link-changes`) for a dataplane port, the kernel's carrier counts for a card Linux holds. | *delivered in 0.152.11* |

## How it is written

**By the code that acts, never from a journal.** Each writer calls
`events.record()` with one event. Nothing reads the journal or FRR's logs to
make events, so routine noise has no way in: the doors daemon's per-request
line, a timer that finds nothing to do, FRR's debug - none of them is a call to
`record()`. The doors daemon does not journal the events' own requests either
(`doors.journaled()`): a `show log` or a polling `GetSystem` would otherwise add
door lines of their own.

**One line, one write, bounded.** `append_event()` writes the event as one JSON
line with one `write(2)` under `O_APPEND`, so two writers interleave whole
lines. When the line would take the file past 256 KiB the file becomes
`events.log.1` (replacing the previous one) and a new file starts; the reader
reads both, oldest first. A torn last line after a power cut is skipped.

**From the management container, through the host's door - not the mount.**
The container does mount `/var/log/prisma-routing`, but the VTY in it runs as
the account that logged in, and the directory is root's 0750 because the CGNAT
trail beside this file names subscribers. Widening it would widen it for the
trail too, and an account writing the file directly could write any name in
it. So the door takes two requests (`doors.answer_event()`):

* `{"events": filters}` - any account reads, filtered on the host;
* `{"event": {...}}` - any account writes, **stamped with its name as the
  kernel reports it (SO_PEERCRED) and with the door's time**. An account
  writes only what it did - a commit, a rollback, a confirmation, an expiry,
  a request - from the VTY or the API; a start, a boot or an upgrade done is
  root's. A request that restarts something (an "asked" naming components) is
  written for an account only if it may restart things: prisma-operator, as
  the restart's own door decides (#563). And at most 30 a minute from one
  account (`ACCOUNT_EVENTS_PER_MINUTE`): the record rotates at a fixed size,
  so an account writing without end - a read-only one may write too - would
  push the box's own history out of it. Root's writes are not counted.

With no daemon listening - the host's own VTY, a lab - the write is local, as
every door falls back.

**Who and from where.** `who` is the account (`vppapi.identity()`, then the
door's stamp). `from` is `ssh <address>` - sshd's `SSH_CONNECTION` on the
host, and in the container the address the login's broker hands the session
door (`prisma-vty-session` sends it, `doors.session_argv()` passes it only if
it is an address) - or `the API` for an API worker (`PRISMA_EVENT_SOURCE`), or
`the box` for the host's own actions. It is the session's assertion, like the
CGNAT trail's `audit_user` (#22): a record of which operator did what among
people trusted to be there, not evidence against them.

**The configuration trail is not duplicated.** `config-history.log` is the
dataplane's hash-chained record of every pool definition, for a court order
(docs/logging.md). The commit event says that a commit happened, by whom, with
its comment; it does not copy definitions, and `show cgnat history` stays where
they are read.

## A restart's cause

**Recorded before the action, matched after it.** Whoever asks for a restart
writes an "asked" event first, naming the cause a start will carry and the
components it restarts:

| Asked by | Cause | Components |
|---|---|---|
| `system dataplane restart` (VTY, or `RestartDataplane`) | `operator` | dataplane, routing - the whole stack goes down (prisma-hot-card) |
| `system upgrade <version>` (VTY, or `Upgrade`) | `upgrade` | dataplane, routing - for whatever its own run restarts |
| `system reboot`, `system shutdown` | `reboot`, `shutdown` | none: the boot after it is the boot, and its reason is `last-reboot` (#534) |

The host's apply - `prismash --load`, which runs at boot and after every
restart of the stack, and `prismash --reapply`, which `watch-the-dataplane`
runs when Docker restarted a container - then reads the clocks `show system
uptime` reads and calls `observe_starts()`. For a start it has not recorded
(`events.state` keeps the last ones), it writes a "start" event, at the time of
the start, whose cause is:

1. `boot`, when the start is within 300 s of the kernel's boot (with the
   boot's recorded reason, when there is one);
2. else the newest "asked" naming the component, at most 900 s before the start
   and no more than 3 s after it, and after the component's previous start -
   one request is one start's cause (the lab killed a dataplane 8 s after an
   operator's restart had brought it up, and with the window alone that start
   was the operator's too);
3. else `unexpected`: nothing on the box asked for it. A warning.

The boot itself is an event too, with its reason as recorded.

**Not twice.** The VTY in the container and the API record the request as the
account that asked, then open the door that runs it on the host - and the
host's VTY, root's, running it there would record it again as root's. The door
starts that VTY with `PRISMA_ASKED_RECORDED=1` (`doors.api_run_argv()`), and
`record_asked()` writes nothing under it. A match by time was the first cut:
`lab/api-grpc.sh` showed it wrong, since root's reboot a minute after opsman's
is a request of its own.

**What it does not guess.** A box upgraded onto 0.151.14 has no `events.state`:
its first reading records the boot (a fact) and any start long after it only as
seen, because whatever caused it happened before anything was recording. Two
starts of one component less than 3 s apart read as one. An upgrade, as shipped
today, restarts neither the dataplane nor the routing daemons (they change
images at the next restart, #528), so its "asked" names them only for a start
within 900 s that nothing more recent explains.

`show system uptime` adds a `started by:` row under the dataplane and the
routing daemons when the record has their start; `GetSystem` carries the same
as `dataplane_start_cause` and `control_plane_start_cause` (`""` when the box
has not recorded that start). A client SHOULD say "restarted by itself" only
for `unexpected`.

## What the box sees: the watcher (phase 2)

**Where it lives.** `prisma-routing-events-watch.service` runs `prismash
--watch-events` on the host, as root, beside `watch-the-dataplane`'s timer.
It is a loop and not a timer: the VTY's import costs 0.28-0.46 s of CPU a
run (measured on the bench on 2026-10-06), which a timer every 10 s would pay
for ever - 3 to 4% of a core. Resident, it pays it once (about 57 MB), and a
tick is its readings: a few `vppctl` at about 11 ms each (no held API
connection, which a dataplane restart would leave dead) and a few `vtysh`.
`Shell.watch_read()` takes the readings; `src/prismash/eventwatch.py`
compares them and is pure, so `test/test_eventwatch.py` drives every decision
with readings built by hand and times pinned.

**Readings compared, never log lines.** Each kind is the box's own state,
read again a few seconds later: what changed between the two readings is an
event, and nothing else is. So the journal's noise still has no way in: a
door call, a timer run or FRR's debug is not a reading.

| Kind | Read from | Every | Source | What it says |
|---|---|---|---|---|
| `link` | `show interface` (admin), `show hardware-interfaces brief` (link) and `show bpa link-changes` (each port's count of link changes); for a card Linux holds, `/sys/class/net/<card>`'s `flags`, `carrier`, `carrier_up_count` and `carrier_down_count` | 10 s | dataplane; host for a card Linux holds | a port's link down or up, saying it is not administrative ("the wire or the far end") and counting the sub-interfaces it took with it; a port or a sub-interface administratively down or up; a link that went down and came back between two readings, with how many times (#588's last phase). A sub-interface's link is its parent's, so it says only its own administrative changes: an ISP's port carries hundreds |
| `transceiver` | the plugin's `show bpa transceiver` for a DPDK port; the kernel's module EEPROM interface (`ethtool -m`'s SIOCETHTOOL) for a card Linux holds, read by the watcher itself; `transceiver.decode()` for both | 60 s | dataplane; host for a card Linux holds | a module inserted (with what it is), removed, swapped; its RX power or temperature past its warning (warning) or alarm (error) threshold, with the level and the threshold, and back within them. A card whose driver has no module to read (virtio, a veth: EOPNOTSUPP) says nothing, ever |
| `bgp` | `show bgp vrf all neighbors json` | 10 s | routing | a session Established, or leaving it with FRR's reason - `lastResetDueTo`, the NOTIFICATION's reason and the peer's shutdown message; and one that went down and came back between two readings, seen by FRR's `connectionsDropped` |
| `bfd` | `show bfd peers [counters] json` | 10 s | routing | a session up, or down with bfdd's diagnostic; and down and back between two readings, by its down counter (at a 300 ms detect time a reading every 10 s sees the state only by luck) |
| `pool` | `show bpa blocks` | 30 s | dataplane | a pool whose refusals grew: out of blocks, once, however long it goes on refusing; and with a block free again once it refuses nobody. A deterministic pool's reserve the same |
| `collector` | `show bpa logging`, through `logstate.LogWatch` | 10 s | dataplane | a TCP stream (syslog, IPFIX over TCP) losing its collector - its connection is not `connected` - or reaching it again. A UDP stream has no connection to see: it is lost when LogWatch calls it stalled (sends failing, nothing delivered for 60 s) |
| `login` | wtmp and btmp | 10 s | host | a login (who, from where, to the VTY or a shell), a logout (info), a failed login (the account typed, from where) |

`show log`'s lines from the lab (`lab/show-log-watch.sh`, 2026-10-06, the
lab's container in UTC):

```
2026-10-07 00:01:01  warning  dataplane  interface memif1/0: link down,
    administratively up: the wire or the far end; its 1 sub-interface(s) with
    it
2026-10-07 00:01:02  notice   dataplane  interface memif1/0: link up, and its 1
    sub-interface(s) with it
2026-10-07 00:01:02  notice   dataplane  sub-interface memif1/0.100 is
    administratively down: shut down by configuration
2026-10-07 00:01:02  warning  dataplane  memif1/0: rx power -19.00 dBm, below
    its low warning (-18.01 dBm)
2026-10-07 00:01:03  warning  dataplane  module removed from memif1/0 (it was
    Rei do SFP RSMPP8510GL-03D (serial FP23H150378))
2026-10-07 00:01:03  notice   dataplane  module inserted in memif1/0: Rei do
    SFP RSMPP8510GL-03D (serial FP23H150378)
2026-10-07 00:01:04  warning  routing    BGP session with 10.9.0.2 (AS 65001)
    left Established: BGP Notification received: Cease/Administrative Shutdown
    ('maintenance window')
2026-10-07 00:01:06  notice   routing    BGP session with 10.9.0.2 (AS 65001)
    is Established
2026-10-07 00:01:07  warning  routing    BFD session with 10.9.0.2 (on veth-a)
    down: control detection time expired
2026-10-07 00:01:07  warning  routing    BGP session with 10.9.0.2 (AS 65001)
    left Established: BFD down initiated
2026-10-07 00:01:09  warning  host       failed login as noc (from ssh
    127.0.0.1)
2026-10-07 00:01:10  notice   host       noc logged in to the VTY (by noc, from
    ssh 127.0.0.1)
2026-10-07 00:01:14  warning  dataplane  CGNAT pool small ran out of blocks: 1
    request(s) for a block refused since the last reading, 4 of 4 blocks in use
```

**A start is not a change of everything.** Within `SETTLE` (60 s) of the
dataplane's start its kinds are not compared, and within 60 s of FRR's start
BGP and BFD are not: a restart is an event of its own (phase 1), and every
link coming up after it is the restart, not news. After the minute the
reading is compared with the one from before the restart, so a link that was
up and is still down a minute after the dataplane came back IS said. Counters
a restart zeroes (FRR's connections, a pool's refusals) are a new baseline
across one.

**Not everything again.** The last readings are kept in
`/var/log/prisma-routing/events-watch.state`, beside `events.state` and
written only when what was seen moved (not for the reading times alone). A
restart of the watcher, or of the box, compares with what it last saw. A
first reading with no state is a baseline and says nothing, as
`observe_starts()`' first reading says nothing of a start long before it.

**Flapping is held, by subject.** Four changes of one subject (an interface,
a module's level, a session, a pool, a stream) in 5 minutes are written one
by one; from the fifth the changes are counted and not written, and at the
end of each 5-minute window one event says them: "interface X flapped 37
times in 5 min, not each recorded; it is now link up". A window with no
change ends the hold. Failed logins from one address are a subject too:
"failed logins from ssh 203.0.113.9: 30 in 7 min, not each recorded". A link
flapping all day writes 4 events and then one every 5 minutes, about 290 a
day, against a record of about 3000.

**Bounded otherwise by the record.** The watcher writes as root, so the
door's 30-a-minute account limit (`doors.ACCOUNT_EVENTS_PER_MINUTE`) does not
apply to it - it is for accounts, and is unchanged - and its events are
bounded by the record's rotation and the flapping rule. No account may write
a watcher's kind: `event_error()` refuses `link`, `bgp`, `login`... from
anyone but root.

**The dataplane given up on.** Not a reading: written by the code that gives
up, as phase 1's events are. `watch-the-dataplane` records an error when
Docker stopped restarting the dataplane and the cards go back to Linux, and
when it stops trying to put the configuration back on a restarted dataplane
(after 3 attempts, once); `prisma-hot-card` records `system dataplane restart`
giving up (the dataplane would not stop) and not finishing (the tuner, the
start, or the configuration not loading in 180 s) - the restart that left
10.40.200.166 dark on 2026-10-06 (#591), which nothing recorded. Kind
`gave-up`, source `host`.

**The SNMP and API settings.** Written by the commit that applies them
(kind `settings`, by the commit's account and session): applied, with what
the apply said, or refused, with why. Not the boot's: the boot's load (and
each restart's, and `--reapply`) applies the same settings again, and an
event per load saying nothing changed is the noise #588 keeps out. An SNMP
refusal at boot is not written when it happens: SNMP is tried again after the
interfaces (#591), and that retry writes how it ended - "applied once the
interfaces were up" (notice), or refused (warning), once, after the last try
(#601). A `snmp listen-address` not on any interface yet at the first apply
- the management address, at every boot of 10.40.200.166 - is waited for, at
most 15 s, and the boot's journal says "snmp: waiting for <address> to come
up"; one that never comes is the refusal, naming it. An API refusal at boot is
written as before: nothing tries it again.

**Decisions taken here, conservatively, that nobody asked for:**

* **`source` keeps the issue's components; `kind` says what an event is.**
  `source` stays who saw it - the dataplane (links, modules, pools, the
  exporter), routing (BGP, BFD), the host (logins, giving up) - so phase 1's
  filters keep their meaning, and `show log kind <kind>` is the new filter.
  Sources `link`, `bgp`... would have split one component across several
  names.
* **Logins from wtmp and btmp, not sshd's log or PAM.** They are fixed-size
  utmp records (glibc's layout, unchanged for decades) that sshd and
  login(1) already write: a login and logout in wtmp, a failed password in
  btmp (OpenSSH's `record_failed_login`). sshd's log lines are prose that
  changes between releases and lives in the journal; a `pam_exec` line in
  `/etc/pam.d/sshd` sees a failure only if placed after the module that
  fails, and the package's upgrade rewrites the file. What they do not
  have: a failed public key (an attempt, not a failure; OpenSSH does not
  record it), and a VTY login is an SSH or a console login whose shell is
  the VTY - the VTY has no password of its own, so "to the VTY" is read
  from the account's shell. A first reading starts at the end of each file:
  history is not news.
* **Modules on the dataplane's ports only** (phase 2). A card Linux holds
  (the management card, an AF_XDP hot card) was read through a door, and a
  door call every minute is the journal noise #588 removed. The last phase
  reads them in the watcher's own process, which is root on the host
  already: no door, nothing in the journal.
* **A UDP collector cannot be seen.** The box sees its sends fail, not a
  collector go away, so "lost" for IPFIX over UDP is LogWatch's stalled
  verdict, which a collector that silently drops what it is sent never
  reaches.
* **Logouts are `info`.** Kept for `last`-like reading, below what an
  operator filters with `severity notice`.

## What stands, where it goes, how it is followed (phase 3)

### What stands when the watcher starts

**Said once a boot.** The watcher records changes, so a condition already
true when it starts was never said: on 10.40.200.166 (2026-10-06) the X520's
RX power stood at -14.8 dBm against a -13.0 dBm low warning before the
watcher first ran, and the record had nothing. Now each kind's first reading
of a boot is also read for what is abnormal in it, and each condition is said
once, in the present tense, as a warning (an alarm as an error):

| Kind | What stands | Not said |
|---|---|---|
| `link` | a port administratively up whose link is down, with the sub-interfaces on it | a port shut by configuration; a sub-interface (its link is its parent's) |
| `transceiver` | a module's RX power or temperature past a warning or an alarm | a module within its thresholds; an empty cage |
| `bgp` | a configured session not Established, with FRR's state and last reset | a session shut by the box's configuration (`neighbor X shutdown`) |
| `bfd` | a session down, with bfdd's diagnostic | a session shut by configuration |
| `pool` | a pool with no block free that has refused requests since the dataplane started | a pool full and refusing nobody - a deterministic pool is that by design |
| `collector` | a configured CGNAT log stream not reaching its collector | a stream not configured |

```
2026-10-07 00:04:12  warning  dataplane  standing at start: enp1s0: rx power
    -14.79 dBm, below its low warning (-13.00 dBm)
```

What `lab/show-log-watch.sh` read back, its first pass started with a port
whose far end never came and a module whose light was already low (the
lab's container in UTC):

```
2026-10-07 03:27:00  warning  dataplane  standing at start: interface memif3/0:
    link down, administratively up: the wire or the far end
2026-10-07 03:27:00  warning  dataplane  standing at start: memif1/0: rx power
    -19.00 dBm, below its low warning (-18.01 dBm)
```

and its second pass - a new process, a restart of the watcher - said
nothing.

* **"Once a boot" is kept in the state file**: events-watch.state carries the
  boot each kind was read for (`stood`), so a restart of the watcher within
  the same boot - `systemctl restart`, an upgrade - says nothing again, and
  the next boot says what stands then. A state file with no `stood` - none at
  all, or one 0.152.0's watcher wrote - says them: what stands was never
  said, which is the X520's case after the upgrade.
* **Not before 180 s** of the component it is read from
  (`STANDING_SETTLE`): a minute after a boot a BGP session is still
  connecting (FRR's connect retry is 120 s), and "not Established" then
  would be the boot, not news. A component whose start is not known is not
  held.
* **Not twice.** A condition the same tick's comparison already said - a link
  that went down while the watcher was stopped, read against the state from
  before - is said once, as the change.
* **Bounded**: 20 a kind, and one more saying how many were not each
  recorded. They are not counted by the flapping rule: once a boot is their
  bound.
* A standing pool is marked out of blocks, so the change that frees it is
  said ("has free blocks again"), as for one that ran out while watched.

### Where it goes: `system` > `logging`

```
prisma# [edit] system
prisma# [edit system] logging
prisma# [edit system / logging] host 192.0.2.10
prisma# [edit system / logging] host 192.0.2.11 protocol tcp severity warning
prisma# [edit system / logging] commit
```

`host <address> [port <n>] [protocol udp|tcp] [severity <level>]
[source-address <address>] [vrf <name>]`, at most four, one per address (a
second `host` to the same address replaces it); `no host <address>` stops
sending there. Port 514 over UDP and 601 over TCP by default; `severity` is
the least severe level sent, `info` (everything) by default; `vrf` names a
`vrf <name> table <n>` of the configuration, refused at commit when it has
none. An address, never a name: the watcher would resolve it at every send,
and the box whose DNS failed is the one whose events most need to leave it.
The API sets it with Edit at `["system","logging"]` and reads it typed in
GetConfig's `SystemConfig.log_destinations`.

**NOT THE CGNAT'S CONNECTION RECORDS.** `nat` > `cgnat` > `logging syslog`
(and IPFIX) exports who held which public port, for a court order: a
compliance stream the dataplane writes and spills to disk when its collector
is away ([docs/logging.md](logging.md)). `system logging` is the box's own
record - commits, restarts, links, sessions, logins - for the NOC's syslog.
Two configurations, two streams, two collectors that SHOULD be different
machines; neither carries the other's lines, and a change to one never
touches the other.

**The wire.** RFC 5424, facility `daemon` (3), the severity mapped (error 3,
warning 4, notice 5, info 6), the hostname the box's, APP-NAME
`prisma-routing`, MSGID the event's kind in upper case, and the event's
fields as structured data under the product's enterprise number - two
messages `lab/system-logging.sh`'s UDP collector received (2026-10-07; the
hostname is the lab container's; one message a line, folded here):

```
<29>1 2026-10-07T03:42:13.839Z f4a5705a7276 prisma-routing - COMMIT
[event@67033 source="vty" kind="commit" account="root"] configuration
committed: first pool (by root)
<28>1 2026-10-07T03:42:13.945Z f4a5705a7276 prisma-routing - GAVE-UP
[event@67033 source="host" kind="gave-up" origin="the box"] the dataplane
was given up on, as a lab says it (from the box)
```

Over UDP one datagram an event; over TCP RFC 6587 octet counting, the CGNAT
exporter's framing (bpa_log_tcp.c says why it is not newlines).

**WHY THE WATCHER SENDS, AND NOT RSYSLOG.** The box has no rsyslog: Debian's
package is not on the medium, and install.sh installs no packages (it is
also the upgrade, which runs with no network). Adding one would make this a
medium release, and leave a second daemon with a generated configuration to
keep in step and to watch. The watcher is already resident, root and on the
host, and reads the record's neighbour files; it follows events.log itself,
between its ticks, every second:

* **The record is the spool.** Each destination keeps its position in
  events.log (inode and offset, `events-forward.state` on the log volume),
  advanced only past what was sent. A TCP collector that is away, a restart
  of the watcher or a reboot loses nothing and repeats nothing: what waited
  is sent when the collector is back, from events.log and events.log.1. A
  collector away for longer than the record is long loses what rotated out
  of it, which the record no longer has either.
* **The event's own fields**, read off the JSON the record keeps: no line is
  parsed back into a severity.
* **A new destination starts at the end**: history is not news, as the
  watcher's first reading of wtmp is not.
* **Nothing restarts.** The commit writes `/etc/prisma-routing/logging.conf`
  (the VTY's words, beside api.env) and the watcher reads it again when it
  changes. A collector that refuses is asked again every 30 s; one send
  waits at most 2 s, so a dead collector never holds the watcher's readings.
* What a TCP stream that broke half way had in flight is sent again: a
  collector may see a repeat, never a gap.

`show system logging` says each destination and what the watcher last said
of it (`/run/prisma-routing/state/logging`, nothing secret in it) - here
the same lab with its TCP collector stopped:

```
127.0.0.1:5514 udp  info and above
  sending
  sent 2 event(s) since the watcher started, the last at 2026-10-07 03:42:16
127.0.0.2:5601 tcp  warning and above
  not reached: connection refused; tried again every 30 s
  sent 0 event(s) since the watcher started
```

The two events said while it was away reached it, once each, at the first
pass after it was back - a new watcher process, as after a restart.

"not forwarding yet: the watcher (prisma-routing-events-watch) has not said"
is a watcher that has not written its status for a minute - stopped, or not
at its pass since the commit. GetSystem carries the same, typed
(`logging`, `logging_text`). The commit is in the record as a `settings`
event ("the logging settings were applied: 2 remote syslog
destination(s)").

### How it is followed: `monitor log`, WatchEvents

```
prisma> monitor log
prisma> monitor log severity warning
prisma> monitor log kind bgp last 5
prisma> monitor log since 10m source routing
```

The record as it is written, a line each in `show log`'s words, until
Ctrl-C (or `seconds <n>`). `show log`'s filters: `severity`, `source` and
`kind` select what is followed; `last` and `since` what is shown first. With
neither, it starts now - not `show log`'s last 50.

It reads the record again every second, through the host's door as `show
log` does, over its last 15 minutes: an event's time is when it HAPPENED,
not when it was appended - a start is written by the host's apply up to
minutes after it, a login carries wtmp's time - so one is said when it
arrives, under its own time, rather than lost behind a newer one. The door
answers a read of the record whole (at most 512 KiB of JSON); a follower is
one such read a second.

`OperationalService.WatchEvents(ListEventsRequest) returns (stream
BoxEvent)` is the same in the API: the request's severity, source and kind
select what is streamed, `last` and `since` what is sent first, and the
stream starts now with neither. INVALID_ARGUMENT, in the VTY's words, for a
filter `show log` refuses.

### SNMP notifications

To every `snmp trap-host` (#507). Standard where a standard one exists and
the box already sends it; the product's own, as DISMAN-EVENT-MIB monitors on
PRISMA-ROUTING-MIB, where it does not:

| The record says | Notification | Whose | Since |
|---|---|---|---|
| a port's link down or up | `linkDown`, `linkUp` | IF-MIB, net-snmp polling the agent's `ifOperStatus` | #507 |
| a BGP session leaving Established, and back | `bgpBackwardTransition`, `bgpEstablished` | BGP4-MIB, bgpd over AgentX | #507 |
| a pool with no block free | `poolExhausted`, with the pool and its utilisation | the product's: `prPoolUtilisation` reaching 100 | 0.152.2 |
| a module past a threshold, and back | `transceiverStateChange`, with the port and its level | the product's: `prXcvrState` changing (normal, warning, alarm, unknown) | 0.152.2 |
| a BFD session down, up, or down and back between two readings | `bfdSessDown`, `bfdSessUp`, with `bfdSessDiag` of its index | BFD-STD-MIB, sent by this watcher over AgentX from the same comparison (#559, docs/bfd.md) | 0.152.3 |

Not sent twice: the links and BGP were already notifications, so phase 3
adds none for them. `poolNearlyFull` (90%) and `portsRefused` stay as they
were. BFD, which phase 3 could not send (no object of PRISMA-ROUTING-MIB
held it, and bfdd serves no SNMP), is BFD-STD-MIB's own pair since 0.152.3:
this watcher hands them to snmpd itself (#559). **Not sent**: logins,
commits and restarts, which have no object to monitor; `show log`, the
syslog destinations and WatchEvents carry them. A monitor fires when its test BECOMES true (docs/snmp-mib.md), so a
pool already out of blocks when snmpd starts is not notified: the record's
standing event says it.

### Decisions taken here, conservatively, that nobody asked for

* **A destination is an address, never a name** (above).
* **Facility `daemon`, fixed.** Apart from the CGNAT records' `local0`, so a
  collector that receives both files them apart; a facility per
  destination can be added when an operator asks for one.
* **`info` (everything) by default.** A collector gets the whole record
  unless the operator narrows it.
* **No event when a syslog destination is lost.** It would be sent to the
  other destinations and counted against the record's rotation for an
  outage `show system logging` and GetSystem already say; the record is the
  spool, so nothing is lost meanwhile.
* **A standing condition says the present tense and keeps the change's
  kind, subject, severity and reason**: a client filtering `kind link`
  sees it; the words "standing at start:" tell it from a change.
* **A 0.152.0 state file says what stands once**, although the boot did not
  change: its watcher never said it.

### What was left, and where it went

* **A BFD notification**: BFD-STD-MIB's own pair, sent by this watcher, in
  0.152.3 (#559).
* **The modules of cards Linux holds** and **a link flap faster than a
  reading**: #588's last phase, below.

## The last phase: Linux's cards, and a flap between two readings

### The cards Linux holds

The watcher reads, beside the dataplane's ports, every PCI card Linux still
holds - `show hardware`'s list less what the dataplane took: the management
card, a spare port - for two things:

* **Its link**: the administrative state from `IFF_UP` in
  `/sys/class/net/<card>/flags`, the link from `carrier`. The same events
  and standing condition as a dataplane port's, named `interface <card>
  (held by Linux)`, source `host`.
* **Its module**: the kernel's module EEPROM interface - SIOCETHTOOL with
  `ETHTOOL_GMODULEINFO` and `ETHTOOL_GMODULEEEPROM`, what `ethtool -m` sends
  - decoded by the same `transceiver.decode()` the dataplane's bytes go
  through: a module inserted, removed or swapped, its RX power or
  temperature past a threshold and back, and what stands at start, in the
  words a dataplane port's are said in, source `host`.

**Read here, not through a door.** The VTY and the API read a Linux card's
module through the host's door, because they run as an account in the
management container; the watcher is root on the host already, so it calls
`transceiver.linux_read()` itself. A door call a minute was the journal noise
phase 2 refused to add.

**A card with no module is quiet.** A virtio or veth card's driver has no
module to read and the kernel answers EOPNOTSUPP: `decode()` calls it
unreadable, and an unreadable module is neither in nor out - no event, no
standing condition, at any reading. A card that has left Linux (given to
the dataplane at its restart) is not read at all, so it is never "a module
removed". An empty cage on a card that can read one answers an I/O error
and is an empty cage: its module going in is an event.

### A flap between two readings

A reading every 10 s compares states, so a link that went down and came
back between two readings reads `up` twice and was never said - the flap an
operator chasing a bad fibre most needs. Two counts make it visible:

* **The dataplane's ports: the plugin counts.** VPP keeps no count of a
  link's changes (`show hardware-interfaces` prints the state and nothing of
  how it got there, VPP 26.06), but every driver's link change goes through
  `vnet_hw_interface_set_flags()`, which calls the functions registered
  with `VNET_HW_INTERFACE_LINK_UP_DOWN_FUNCTION`. The plugin registers one
  (`src/plugins/bpa/bpa_linkcount.c`) and `show bpa link-changes` prints,
  per port, its state and how many times its link came up and went down
  since the port was created:

  ```
  interface memif5/0 link up ups 2 downs 1
  ```

  A port deleted and created again starts from zero, and the watcher takes
  a count that went backwards - or one read across a dataplane start - as a
  new baseline, never as a flap. A dataplane plugin older than the command
  answers "unknown input", and its ports are compared by their state alone,
  as before.
* **A card Linux holds: the kernel counts.** `carrier_up_count` and
  `carrier_down_count` (Linux 4.16 and later) beside `carrier`.

A port whose link reads the same twice but whose count moved says so, with
how many times - what `lab/show-log-watch.sh` read back (2026-10-11, the
lab's container in UTC), a memif's far end and a veth's taken away and put
back with no reading between:

```
2026-10-11 06:34:25  warning  dataplane  interface memif5/0: link went down and
    came back between two readings (1 time(s)): the wire or the far end
2026-10-11 06:34:25  warning  host       interface veth-c (held by Linux): link
    went down and came back between two readings (1 time(s)): the wire or the
    far end
```

and a change of state that took more than one transition says how many
(`interface X: link down, administratively up: the wire or the far end (3
changes since the last reading)`). The flap is held and counted like any
other change of the subject (`flap_admit()`): a port flapping all day is one
summary event a window, not one a reading.

**What it cannot see**: a change the driver never reported. VPP's DPDK
ports are polled for their link by VPP's dpdk process every few seconds,
so a flap shorter than that poll is not a change VPP saw either - the card's
own registers saw it, and nothing above them did.

### Decisions taken here, conservatively, that nobody asked for

* **Source `host` for a card Linux holds**, `dataplane` for the
  dataplane's ports: `show log source host` reads the host's cards with its
  logins. `kind` is `link` and `transceiver` for both.
* **A flap between two readings is a warning**, as a link down is: the
  link was down, for however short a time.
* **One event for a reading's flaps**, with the count, not one per flap:
  the count is what the reading knows; when each happened, it does not.
* **The cards are the PCI ones**, as `show hardware` lists them; a lab's
  veth is named by `PRISMA_WATCH_NETDEVS`, which a box never sets.

## The PPPoE control plane (#597)

The `prisma-bng` container (docs/bng.md) is a component like the dataplane
and the routing daemons, and its runs are in the record:
every start, with its cause; every exit, with its code and why; and each
`aaa radius` server that accel-ppp marked failed, and used again.

**Its runs are a record, not a state.** Docker brings the container back
within a second (`restart: unless-stopped`), so a reading every 10 s of
"is it running" reads `true` on both sides of a crash; and docker resets
`State.ExitCode` to 0 at the start that follows, so the exit code is gone
by the next reading (measured in `lab/pppoe.sh`, 2026-10-13). So the container writes
its own: its entrypoint (`appliance/prisma-bng-entrypoint`, `run_note`)
appends one line per start and per exit to `/run/prisma-routing/bng/runs`
- the host's `/run`, mounted into the container, so it outlives every
restart and not the boot:

```
3 1792015334 start
4 1792015352 exit 1 accel-pppd was killed by signal 9 (KILL)
5 1792015353 start
```

The exit's reason is the entrypoint's own word for what it saw: accel-pppd's
exit code, or the signal that killed it (by name; "the OOM killer" when the
container's cgroup counted an OOM kill since accel-pppd started), the
dataplane losing the control-plane TAP, a start that could not finish
(no configuration, the dataplane not answering, the plumbing refused), or a
SIGTERM - a stop. A run killed from outside (`docker kill`, a stop that ran
out of its 20 s) writes nothing, and the next start says so. The file keeps
the newest 32 lines; the watcher reads every line past the last one it saw,
and a numbering that went back is a new boot's.

**A start's cause is found the way a dataplane start's is.** A commit that
starts, restarts or stops the container writes an `asked` event naming
component `bng` BEFORE the host is asked (`Shell.record_bng_asked`), and so
does the load that rewrites an older release's files (`pppoe.FORMAT`); the
watcher takes the newest `asked` before the start, after the previous one,
as `cause_of()` does for the dataplane. Nothing asked, near the boot: the
boot. Nothing asked otherwise: `unexpected`, and the sentence says what
came before - the exit the restart policy answered, or a run that recorded
no exit. An account's `asked` for `bng` alone is taken from any account,
not only prisma-operator's: whoever may commit `pppoe` restarts it.

**RADIUS is accel-ppp's own word**, its `show stat` (`prisma-bng summary`):
each server's state - `active`, or `failed` for its dead-time - and its
`fail count`, so a server that failed and came back between two readings
is said too. Read only while the container's record says it runs and `aaa
radius` has a server, so a box with no PPPoE never runs a `docker exec`;
across a restart of accel-pppd (its uptime went back) every server is
active again, and that is a baseline, not news. A server failed when the
watcher starts is said once, standing.

| `kind` | `component` | Severity | Sentence |
|---|---|---|---|
| `asked` | `bng` | notice | `PPPoE's configuration committed`, `PPPoE removed by a commit`, `PPPoE's files from an older release (format 4) written again in format 5` |
| `start` | `bng` | notice | `the PPPoE control plane (prisma-bng) started: PPPoE's configuration committed by ana from ssh 192.0.2.7` |
| `start` | `bng` | warning | `the PPPoE control plane (prisma-bng) started again, by its restart policy, after it exited: accel-pppd was killed by signal 9 (KILL)` |
| `exit` | `bng` | notice | `the PPPoE control plane (prisma-bng) stopped: stopped (SIGTERM): a restart, an upgrade or no pppoe` |
| `exit` | `bng` | warning | `the PPPoE control plane (prisma-bng) exited with code 1: accel-pppd was killed by signal 9 (KILL)` |
| `radius` | | warning | `RADIUS server 192.0.2.10 failed: it did not answer, and accel-ppp uses the others for its dead-time` |
| `radius` | | notice | `RADIUS server 192.0.2.10 is active again: its dead-time is over and accel-ppp sends to it` |

Source `host`: the host's watcher says them. Starts and exits are held and
counted like any subject's changes (`flap_admit()`), starts and exits apart,
so a crash loop is four of each in five minutes and then one summary a
window.

```
prisma> show log kind exit
prisma> show log kind start since 1d
prisma> show log kind radius
```

**What it does not do.** It does not say a session dropped: sessions are
accel-ppp's and RADIUS accounting's, thousands a day. And a reading of
RADIUS is a `docker exec` every 10 s while PPPoE runs with RADIUS - what
`show pppoe summary` costs once.

## Reading it

```
prisma> show log
prisma> show log last 20
prisma> show log since 2h
prisma> show log since 2026-10-05
prisma> show log severity warning
prisma> show log source dataplane
prisma> show log since 1d source vty | match rollback
prisma> show log kind bgp
prisma> show log kind link since 1h
```

With neither `last` nor `since`, the last 50. `since` takes `45s`, `30m`,
`1h`, `2d`, `1w` or an ISO date or time (`2026-10-05`, `2026-10-05T14:00`, with
`Z` or an offset; none is the box's own zone, which is what the times print
in). `severity` is `error`, `warning`, `notice` or `info`, and shows that level
and every one above it. `source` is `vty`, `api`, `host`, `dataplane` or
`routing`. `kind` is what an event is: phase 1's `commit`, `rollback`,
`confirm`, `confirm-expired`, `asked`, `start`, `boot`, `upgraded`,
phase 2's `settings`, `link`, `transceiver`, `bgp`, `bfd`, `pool`,
`collector`, `login`, `gave-up`, and the PPPoE control plane's `exit` and
`radius` (#597). An event wider than the console folds, its continuation indented,
as every row the VTY prints does (test_prismash_refusals.py holds them to 79
columns): the first row carries the time, severity, source and the start of
the sentence, so `| match` finds an event by those; `source` and `severity`
select whole events. One unfolded line per event was the first cut, and the
width reader refused it - a 113-column row is folded by the operator's window,
inside the words. `ListEvents` gives each event's sentence whole.

A session from the lab (`lab/show-log.sh` through `tools/run-all-labs.sh`,
2026-10-06; the lab's container runs in UTC):

```
2026-09-24 09:23:19  notice   host       the box booted; the reason recorded:
    not recorded
2026-10-06 07:43:23  notice   vty        configuration committed: first pool
    (by root)
2026-10-06 07:43:25  notice   vty        configuration committed (by root)
2026-10-06 07:43:26  notice   vty        rollback to revision 1 committed (by
    root)
2026-10-06 07:43:26  notice   vty        configuration committed, to be
    confirmed within 1 minute(s) or rolled back (by root)
2026-10-06 07:43:27  warning  host       a commit confirmed 1 was not confirmed
    in time: the configuration before it was put back (from the box)
2026-10-06 07:43:29  notice   vty        system dataplane restart asked (by
    root)
2026-10-06 07:43:30  notice   dataplane  the dataplane started again: system
    dataplane restart asked by root
2026-10-06 07:43:30  notice   routing    the routing daemons (FRR) started
    again: system dataplane restart asked by root
2026-10-06 07:43:37  warning  dataplane  the dataplane started again and
    nothing on the box asked for it: unexpected
```

`ListEvents` takes the same five filters (`last` 0 is the default, as at the
prompt; `kind` since 0.152.0), answers each event as fields - `time`,
`severity`, `source`, `text`, `account`, `origin`, `kind`, `component`,
`cause`, and since 0.152.0 `subject` (the interface, peer, pool, stream or
account it is about) and `reason` (FRR's reset, bfdd's diagnostic,
`administrative` or `link`, the level a module crossed, a flapping subject's
count) - and `show log`'s screen for the same filters in `text`. INVALID_ARGUMENT, in the VTY's words, for a
filter the VTY refuses.

## How it is tested

* `test/test_events.py` - the record, its bound, the filters, the cause, the
  door's stamping and what an account may write, the journal left alone.
* `test/test_prismash_units.py`, `test/test_prismash_confirm.py` - `show log`
  through the VTY's own dispatch and pipe, the commit, rollback, confirmation
  and expiry events, the restart asked, the start observed and its cause in
  `show system uptime`.
* `test/test_apiserver.py`, `test/test_doors.py`, `test/test_vty_session.py`
  - ListEvents' filters and fields, the cause in GetSystem, the API's request
  recorded before the host is asked, the session's address.
* `lab/show-log.sh` - commits, a rollback, an expiry, a `system dataplane
  restart` and a dataplane killed and started again, read back with every
  filter; its off run points the record at a path nothing can write.
* `lab/api-grpc.sh`'s phase for #588 - ListEvents against what the API's own
  phases did, phase 5's requests through the real doors daemon, and the cause
  in GetSystem.
* `test/test_eventwatch.py` (phase 2) - each kind's reading and comparison,
  the flapping rule, the settle, the state, the login records, the Shell's
  readings from the box's own screens, a tick and the loop, the settings.
* `test/test_watch_dataplane.sh`, `test/test_prisma_hot_card.sh` (phase 2) -
  the dataplane given up on, and a restart that did not finish, in the record.
* `test/test_eventwatch.py` (phase 3) - what stands, kind by kind, once a
  boot and not again on a restart of the watcher, after STANDING_SETTLE, not
  twice with a change, bounded; the loop forwarding between its ticks.
* `test/test_eventfwd.py` (phase 3) - a destination by the prompt's and the
  file's one rule, the RFC 5424 wire and its framing, the record followed
  across its rotation, a real UDP and TCP collector, a TCP outage that loses
  nothing and repeats nothing, `show system logging`'s rows.
* `test/test_events.py`, `test/test_prismash_units.py`,
  `test/test_apiserver.py` (phase 3) - the follower (`follow_new`), `monitor
  log`'s words and its screen with times pinned, `system` > `logging` at the
  prompt, in a file and in a commit, WatchEvents' plan and GetSystem's state.
* `lab/system-logging.sh` (phase 3) - two collectors configured from the VTY,
  a UDP and a TCP one, each getting its severity, the TCP one away and back
  with nothing lost, `show system logging`, and `monitor log` following the
  record; its off run points the record at a path nothing can write.
* `lab/api-grpc.sh`'s phase for #588 phase 3 - WatchEvents streaming what is
  recorded while it is open, `last` first, a kind refused; `system logging`
  set with Edit, read in GetConfig and GetSystem.
* `test/test_bpa_linkcount.c` (the last phase) - the plugin's count of a
  port's link changes: a repeated report counted once, a port made again
  starting from zero.
* `test/test_eventwatch.py` (the last phase) - `show bpa link-changes`
  parsed against bpa_linkcount.c's own format string, a flap between two
  readings said with its count, a count that went backwards or crossed a
  dataplane start taken as a baseline, a sysfs of Linux cards read, and a
  Linux card's module read through a stand-in ioctl: present with its light
  low (standing), taken out, put in another card, a card that left Linux,
  and a virtio card quiet over three readings.
* `lab/snmp-traps.sh` (phase 3) - `poolExhausted` and
  `transceiverStateChange` received by snmptrapd.
* `lab/show-log-watch.sh` (phases 2 and 3) - what stands at start: a port
  whose far end never came and a module whose light starts low, said once by
  the first pass and not by the second; a memif's far end deleted and put back,
  a sub-interface shut, a stand-in module's light low, taken out and put
  back, a BGP session shut by its peer with a message, the peer's bfdd
  killed, a wrong SSH password and a right one, a four-block pool given five
  subscribers, a link flapped 14 times, each read back from `show log` with
  its reason; idle passes write nothing. Its off run points the record at a
  path nothing can write. Since the last phase it also takes a second
  memif's far end away and puts it back with no pass between - the plugin
  counts it, `show log` says the flap with its count - flaps a veth that
  `PRISMA_WATCH_NETDEVS` names as a card Linux holds, reads its link going
  down as a state, and finds nothing said of its module (the kernel answers
  EOPNOTSUPP, as for a virtio card).

### The mutations, each shown red and put back

Measured on 2026-10-06 (phases 1 and 2) and 2026-10-07 (phase 3) by
applying each to the tree, running the suite named, and restoring the file.

| Mutation | Red in |
|---|---|
| `append_event()` without the rotation | test_events.py: the file grew past the cap |
| `severity_rank()` inverted | test_events.py: `severity warning` let `notice` through |
| `cause_of()` never reading the "asked" events | test_events.py: 5 checks, every cause `unexpected` |
| `cause_of()` without `after` | test_events.py: a second start took the first one's request |
| `answer_event()` keeping the caller's `who` | test_events.py: an account's event said `root` |
| `journaled()` always true | test_events.py: the events' door calls reached the journal |
| an account may write a start | test_events.py: `event_error()` let a VTY "start" through |
| `observe_starts()` guessing a cause for a start before the record | test_events.py: a first reading said `unexpected` |
| `format_event()` not folding | test_events.py: a row past 79 columns |
| `record_asked()` ignoring `PRISMA_ASKED_RECORDED` | test_prismash_units.py: the host's run recorded the request again |
| `show_or_pass()` without the `show log` branch | test_prismash_units.py: 4 checks, the line went to FRR |
| `uptime_rows()` without the `started by:` row | test_prismash_units.py |
| `_do_commit()` not recording the commit | test_prismash_confirm.py: 3 checks |
| `do_rollback()` carrying `True`, not the revision | test_prismash_confirm.py: "rollback to revision True" |
| `revert_confirm()` not recording the expiry | test_prismash_confirm.py |
| `uptime_fields()` without the causes | test_apiserver.py |
| `hostrun()` not recording the request | test_apiserver.py |
| `session_argv()` passing a `from` that is not an address | test_doors.py |
| the door's run of an API request not marked as recorded | test_doors.py |
| `link_changes()` not telling administrative from link | test_eventwatch.py: 2 checks, a sub-interface and a port shut down said nothing |
| `module_changes()` comparing nothing but presence | test_eventwatch.py: 3 checks, rx power past its warning, back, and no light |
| `bgp_changes()` without FRR's counter | test_eventwatch.py: a flap between two readings said nothing |
| `bfd_changes()` leaving the diagnostic out | test_eventwatch.py: "down" with no reason |
| `pool_reading_carry()` not carrying `exhausted` | test_eventwatch.py: 2 checks, the pool said out of blocks at every reading |
| `read_utmp()` starting a first reading at 0 | test_eventwatch.py: 4 checks, history said as news |
| `flap_admit()` always True | test_eventwatch.py: 4 checks, 59 events for 59 changes |
| `tick()` without the settle | test_eventwatch.py: 3 checks, a restart's links and sessions compared |
| `watch_events()` not reading its state file | test_eventwatch.py: a restart of the watcher said nothing it should, nor kept the baseline |
| `record_settings()` writing the boot's routine apply | test_eventwatch.py: 2 checks |
| an account may write a watcher's kind | test_eventwatch.py: `event_error()` let a VTY "link" through |
| `parse_show_args()` without `kind` | test_apiserver.py: 2 checks, ListEvents' kind refused |
| `event_pb_fields()` without subject and reason | test_apiserver.py |
| `watch-the-dataplane`'s two records replaced by `true` | test_watch_dataplane.sh: each, its check |
| `prisma-hot-card`'s record of an unfinished restart taken out | test_prisma_hot_card.sh |
| `appliance/upgrade`'s record line replaced by `true`; and the line before it without `\|\| true` | test_upgrade_artifact.sh: no upgrade in the box's record; the second killed the upgrade itself under `set -e`, which is how it was found |
| the contract reader without the check for a name defined twice, over a proto with a second `Event` | test_api_contract.py (the shape the first lab run of this phase met: protoc refused the contract, no stubs) |
| `standing_due()` never true (phase 3) | test_eventwatch.py: 4 checks, nothing standing said, not even after a boot |
| `standing_due()` not reading `stood` | test_eventwatch.py: 5 checks, what stands said again at every reading and by a restart of the watcher |
| `standing_due()` without STANDING_SETTLE | test_eventwatch.py: a session said "not Established" while FRR was still starting |
| `tick()` not leaving out what the same tick's change said | test_eventwatch.py: a link said twice, as a change and as standing |
| `bgp_standing()` saying a session shut by configuration | test_eventwatch.py |
| `pool_standing()` without the refusals | test_eventwatch.py: 2 checks, a full pool refusing nobody said out of blocks |
| `standing_changes()` unbounded | test_eventwatch.py: 25 conditions, 25 events |
| `watch_starts()` without the boot | test_eventwatch.py |
| `run()` advancing a destination's position on a failed send | test_eventfwd.py: 4 checks, what waited for a TCP collector never sent |
| `frame()` sending TCP unframed | test_eventfwd.py |
| `rfc5424()` not mapping the severity | test_eventfwd.py: 5 checks, every event daemon.info |
| `read_new()` not reading the rotated file's rest | test_eventfwd.py: 2 checks, the events before a rotation lost |
| `link_changes()` skipping a port whose state did not move (the last phase) | test_eventwatch.py: 4 checks, a flap between two readings said nothing |
| `link_transitions()` without the check for a count that went backwards | test_eventwatch.py: a port made again said as a flap |
| `link_transitions()` ignoring `restarted`; and ignoring it for Linux's cards too | test_eventwatch.py: a dataplane start's counts said as flaps; a Linux card's flap across one lost |
| `linux_link_reading()` without the kernel's counts | test_eventwatch.py: 3 checks |
| `watch_read("link")` without the plugin's counts; without Linux's cards | test_eventwatch.py: 2 checks each |
| `watch_read("transceiver")` reading the plugin's ports only, as phase 2 did | test_eventwatch.py: 3 checks, the Linux card's module, its standing and its removal never read |
| `transceiver_facts(local=True)` keeping a card that left Linux | test_eventwatch.py: a card given to the dataplane said "module removed" |
| `module_changes()` naming a Linux card's module the dataplane's | test_eventwatch.py |
| `module_reading()` taking an unreadable (virtio) module as an empty cage | test_eventwatch.py: 2 checks |
| `bpa_linkcount_note()` counting a repeated report; `bpa_linkcount_reset()` keeping the history | test_bpa_linkcount.c: 2 checks each |
| `wanted()` always true | test_eventfwd.py: 3 checks, a notice sent to a warning collector |
| `parse_host()` taking a name | test_eventfwd.py: 3 checks |
| a new destination starting at the record's start | test_eventfwd.py: 6 checks, history sent as news |
| `destinations()` not falling back to the watcher's list | test_eventfwd.py: a read-only account told there is no destination |
| `stage_logging()` appending a second host to one address | test_prismash_units.py: 3 checks |
| the commit not writing logging.conf | test_prismash_units.py: 6 checks |
| `running_config()` not reading logging.conf | test_prismash_units.py: 3 checks, a commit that would take the destinations away |
| `watch_events()` not forwarding | test_eventwatch.py |
| `follow_new()` following only past the newest time it said | test_events.py, and test_prismash_units.py's `monitor log`: a start written late never said |
| `parse_follow_args()` keeping `show log`'s last 50 | test_events.py |
| `monitor_log()` not taking what is there as seen | test_prismash_units.py: 2 checks, the record's past shown as it was written |
| `events_watch_plan()` sending a backlog by default | test_apiserver.py: 2 checks |
| `logging_state()` ignoring the watcher's status | test_apiserver.py |
| `snmp_monitors()` without `poolExhausted` | test_prismash_system.py: 2 checks |
| `transceiverStateChange` without `-D` | test_prismash_system.py: it would fire once, at the first warning, and never on the way back |
