# Operate

What to do with the machine once it is in production: day-to-day tasks, diagnosis when something breaks, and the record retention the law requires.

| Page | Answers |
|------|---------|
| [Day-to-day recipes](/s/wiki-en/doc/day-to-day-recipes-KCc5cOuBip) | Concrete tasks, with the commands in order |
| [Operations and troubleshooting](/s/wiki-en/doc/operations-and-troubleshooting-kql3GmIqmX) | Where to start when someone complains, and the boot order |
| [The box came up and does not forward](/s/wiki-en/doc/the-box-came-up-and-does-not-forward-TecpM6vyxR) | What the self-test says, where the box tells you why the dataplane stopped, and how to get it forwarding again |
| [When a subscriber's block fills up](/s/wiki-en/doc/when-a-subscribers-block-fills-up-NvSSgpWda1) | The three refusals, extended blocks and per-subscriber limits |
| [NAT behavior and compatibility](/s/wiki-en/doc/nat-behavior-and-compatibility-GcSwkjjgUa) | Whether games, cameras and peer-to-peer work behind this CGNAT |
| [Protocols and ALGs](/s/wiki-en/doc/protocols-and-algs-z0NTWXgdda) | What passes through on its own, what needs an ALG and what is not supported |
| [Port forwarding](/s/wiki-en/doc/port-forwarding-uB5xjjtTNS) | Opening an inbound port without selling a public address |
| [NAT64 — IPv6-only subscribers in the same public space](/s/wiki-en/doc/nat64-ipv6-only-subscribers-in-the-same-public-space-ryyEIrqWdV) | Putting a NAT64 pool into service for IPv6-only subscribers: routes, PREF64, DNS64, records by prefix and the pair |
| [uRPF and spoofed sources](/s/wiki-en/doc/urpf-and-spoofed-sources-UlWGqYZ6T6) | The defense that keeps the log from being confidently wrong |
| [Expanding capacity without dropping subscribers](/s/wiki-en/doc/expanding-capacity-without-dropping-subscribers-R1zYvsfLCT) | The path that costs an outage and the one that does not |
| [Taking the box to a new version](/s/wiki-en/doc/taking-the-box-to-a-new-version-spJOdnrvOD) | `system upgrade`, the package by hand, and when reinstallation is unavoidable |
| [Setting up an active/standby pair](/s/wiki-en/doc/setting-up-an-activestandby-pair-YclNlmh15V) | Two boxes, VRRP on both ports, the pair file and how to test the failover |
| [Management in a VRF of its own](/s/wiki-en/doc/management-in-a-vrf-of-its-own-tTlK811qdt) | Put the management card in a VRF of its own, so the main table's default is the subscribers' and management keeps its own gateway |
| [Monitoring and SNMP](/s/wiki-en/doc/monitoring-and-snmp-GxrrkTSEJT) | What your NMS sees, and what is missing to see everything |
| [Sending the box's event log to the NOC's syslog](/s/wiki-en/doc/sending-the-boxs-event-log-to-the-nocs-syslog-ZSBceClGhA) | What the box records (commits, restarts, links, sessions, logins), how to read it with `show log` and send it to the NOC's syslog with `system` > `logging` — not the CGNAT records |
| [IPFIX logging and audit trail](/s/wiki-en/doc/ipfix-logging-and-audit-trail-mrQPyuvc8g) | Where the records go (IPFIX or syslog), what they carry, and how to prove the trail was not edited |
| [Where the records go: collector, transport and retention](/s/wiki-en/doc/where-the-records-go-collector-transport-and-retention-oXaUx9z75V) | Why the box exports and does not store, which transport to choose, what the collector must keep and what happens when it goes down |

# The three rules that prevent incidents


1. **Changing a pool's mapping drops all of its sessions.** Prefix, `block-size`, `ports`, `policy`, `mode`, `reserve` or VRF: the pool is rebuilt. When subscribers of a deterministic pool would change address or block, `commit` refuses and shows how many, until it hears `commit remap`; in the other cases — BPA pool, VRF change, mode change — it applies without asking.
2. **A remote change goes with** `**commit confirmed**`**.** If you cut your own access, the machine recovers on its own — the one that undoes it is the equipment, not your session, so leaving the VTY or losing SSH cancels nothing. Only a new `commit` confirms.
3. `**commit**` **is not** `**save**`**.** The first makes it take effect now; the second makes it survive a boot. After upgrading the version, `save` before rebooting: the box may refuse the file saved in the old form and come up without configuration.

# See also

* [show commands](/s/wiki-en/doc/show-commands-3WvebaLZkm) — the reference for the diagnostic commands
* [Pool configuration](/s/wiki-en/doc/pool-configuration-wkJ1DcmG2Y) — which fields drop sessions and which do not
