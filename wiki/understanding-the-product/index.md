# Understanding the product

Why the product is the way it is. Read when you need to decide on architecture or assess whether block-based CGNAT fits your case.

| Page | Answers |
|------|---------|
| [Prisma Routing — overview](/s/wiki-en/doc/prisma-routing-overview-GRVBysg8Mo) | What the product is, what it is made of and what does not exist yet |
| [Deterministic and BPA — concepts and mapping](/s/wiki-en/doc/deterministic-and-bpa-concepts-and-mapping-NMOHVImQiU) | Deterministic (block by formula) and BPA (block allocated on demand): the difference, the math and what each one requires |
| [High availability](/s/wiki-en/doc/high-availability-cMUVBBTFgL) | The active/standby pair with VRRP: how to build it, what the switchover preserves and what it drops |

# The idea in one sentence

Conventional CGNAT logs every flow and the log becomes the most expensive item in the system. Block-based CGNAT gives each subscriber a contiguous block of ports, and the log drops to one record per block with BPA, which allocates blocks on demand, or to zero with deterministic, which calculates the block instead of allocating it. BPA is only the dynamic mode; deterministic is not a kind of BPA.

# See also

* [IPFIX logging and audit trail](/s/wiki-en/doc/ipfix-logging-and-audit-trail-mrQPyuvc8g) — the practical consequence of that choice
