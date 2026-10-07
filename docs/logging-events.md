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

FRR's `show logging` is unchanged: each routing daemon's logging
*configuration*, as before. Phase 1 is delivered in 0.151.14 and phase 2 in
0.152.0; the issue stays open for phase 3 below.

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
| **3** | Where the record goes and how it is followed: `system logging` in Prisma's terms (a remote syslog, its level, which sources), replacing FRR's per-daemon `log syslog` as what an operator configures; `monitor log`, a follow mode like `monitor cgnat logging`, and a streaming `WatchEvents` in the API; SNMP notifications for the events PRISMA-ROUTING-MIB has an object for (#507's traps). And what phase 2 left: the modules of cards Linux holds (read through a door today), and a link flap faster than a reading on a port with no counter of its own. | planned |

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
| `link` | `show interface` (admin) and `show hardware-interfaces brief` (link) | 10 s | dataplane | a port's link down or up, saying it is not administrative ("the wire or the far end") and counting the sub-interfaces it took with it; a port or a sub-interface administratively down or up. A sub-interface's link is its parent's, so it says only its own administrative changes: an ISP's port carries hundreds |
| `transceiver` | the plugin's `show bpa transceiver`, `transceiver.decode()` | 60 s | dataplane | a module inserted (with what it is), removed, swapped; its RX power or temperature past its warning (warning) or alarm (error) threshold, with the level and the threshold, and back within them |
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
event per load saying nothing changed is the noise #588 keeps out. A refusal
at boot is written, and so is the retry after the interfaces (#591).

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
* **Modules on the dataplane's ports only.** A card Linux holds (the
  management card, an AF_XDP hot card) is read through a door, and a door
  call every minute is the journal noise #588 removed. Left for phase 3,
  with a reader that needs no door.
* **A UDP collector cannot be seen.** The box sees its sends fail, not a
  collector go away, so "lost" for IPFIX over UDP is LogWatch's stalled
  verdict, which a collector that silently drops what it is sent never
  reaches.
* **Logouts are `info`.** Kept for `last`-like reading, below what an
  operator filters with `severity notice`.

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
`confirm`, `confirm-expired`, `asked`, `start`, `boot`, `upgraded`, and
phase 2's `settings`, `link`, `transceiver`, `bgp`, `bfd`, `pool`,
`collector`, `login`, `gave-up`. An event wider than the console folds, its continuation indented,
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
* `lab/show-log-watch.sh` (phase 2) - a memif's far end deleted and put back,
  a sub-interface shut, a stand-in module's light low, taken out and put
  back, a BGP session shut by its peer with a message, the peer's bfdd
  killed, a wrong SSH password and a right one, a four-block pool given five
  subscribers, a link flapped 14 times, each read back from `show log` with
  its reason; idle passes write nothing. Its off run points the record at a
  path nothing can write.

### The mutations, each shown red and put back

Measured on 2026-10-06 by applying each to the tree, running the suite named,
and restoring the file.

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
